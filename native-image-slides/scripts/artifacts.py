"""Read-only provenance checks and exact-byte ZIP packaging. Never edits pixels."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import zipfile
import zlib


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def contained(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("Path escapes run directory")
    return path


def png_size(path):
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Not a PNG")
    offset, size, image_data = 8, None, False
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 8 + length
        if end + 4 > len(data):
            raise ValueError("Truncated PNG")
        chunk = data[offset + 4:end]
        if zlib.crc32(chunk) & 0xffffffff != struct.unpack(">I", data[end:end + 4])[0]:
            raise ValueError("PNG checksum mismatch")
        if offset == 8:
            if kind != b"IHDR" or length != 13:
                raise ValueError("Invalid PNG header")
            size = struct.unpack(">II", data[offset + 8:offset + 16])
            if not all(size):
                raise ValueError("Empty PNG dimensions")
        image_data |= kind == b"IDAT" and length > 0
        offset = end + 4
        if kind == b"IEND":
            if length or offset != len(data) or not image_data:
                raise ValueError("Invalid PNG ending")
            return size
    raise ValueError("Missing PNG ending")


def _artifact_target(item):
    return item.get("target", item.get("file"))


def check(root, run, mode=None):
    """Checks records, not truth of Web GPT attachment use or visual quality."""
    mode = mode if mode is not None else run.get("mode", "production")
    if mode not in ("production", "acceptance"):
        raise ValueError("Mode must be production or acceptance")
    root = Path(root).resolve()
    errors, seen, chats, hashes, paths = [], {}, {}, set(), set()
    pages = run.get("planned_pages", [])
    selected = run.get("selected_pages", [])
    if not isinstance(pages, list) or not pages or len(set(pages)) != len(pages):
        errors.append("Missing or duplicate planned pages")
        pages = []
    if not isinstance(selected, list) or len(set(selected)) != len(selected) or not set(selected) <= set(pages):
        errors.append("Invalid or duplicate selected planned pages")
        selected = []
    if mode == "acceptance" and len(set(selected)) < 3:
        errors.append("Need at least three selected planned pages")
    sources = run.get("inputs", [])
    if not isinstance(sources, list) or len(sources) < 2:
        errors.append("Missing original manuscript/reference inputs")
        sources = []
    for item in sources:
        try:
            if digest(contained(root, item["file"])) != item["sha256"]:
                errors.append("Input hash changed: " + item["file"])
        except (OSError, KeyError, ValueError) as exc:
            errors.append(str(exc))
    chain_depth, final_pages, repairs = {}, set(), 0
    artifacts = run.get("artifacts", [])
    if not isinstance(artifacts, list):
        errors.append("Artifacts must be a list")
        artifacts = []
    for item in artifacts:
        try:
            if not isinstance(item, dict):
                raise ValueError("Artifact metadata must be an object")
            name, page, stage = item["file"], item["page"], item["stage"]
            if not isinstance(name, str) or Path(name).suffix.lower() != ".png":
                raise ValueError("Invalid PNG filename: " + str(name))
            if name in seen or name in paths:
                raise ValueError("Duplicate output: " + name)
            paths.add(name)
            if page not in selected or stage not in ("final", "reverse", "repair"):
                raise ValueError("Unexpected page/stage: " + name)
            target = _artifact_target(item)
            if not isinstance(target, str) or Path(target).suffix.lower() != ".png":
                raise ValueError("Invalid target PNG: " + name)
            contained(root, target)
            if not isinstance(item.get("qa_passed"), bool):
                raise ValueError("qa_passed must be a boolean: " + name)
            path = contained(root, name)
            actual = digest(path)
            if actual != item["sha256"] or actual in hashes:
                raise ValueError("Changed or duplicated output bytes: " + name)
            size = png_size(path)
            chat, package = item["chat_url"], item["package"]
            chat_was_used = chat in chats
            if not re.fullmatch(r"https://chatgpt\.com/(?:g/[^/]+/)?c/[a-zA-Z0-9-]+", chat):
                raise ValueError("Missing real chat URL: " + name)
            if chat in chats and chats[chat] != package:
                raise ValueError("Chat reused across packages")
            chats[chat] = package
            prompt = contained(root, item["prompt"])
            if not prompt.is_file() or not prompt.read_text(encoding="utf-8-sig").strip():
                raise ValueError("Missing actual prompt")
            if not item.get("runtime_evidence"):
                raise ValueError("Missing observed runtime evidence")
            if stage == "final":
                if page in final_pages or item.get("source") or target != name:
                    raise ValueError("Duplicate final or unexpected source")
                final_pages.add(page)
                chain_depth[name] = 0
            else:
                parent = seen.get(item.get("source"))
                if not parent or parent["page"] != page:
                    raise ValueError("Source must be a previous real image on same page")
                if item.get("source_sha256") != parent["sha256"]:
                    raise ValueError("Source hash does not match actual parent")
                if size != parent["size"]:
                    raise ValueError("Image size changed in edit")
                if stage == "repair":
                    if chat_was_used:
                        raise ValueError("Repair must use a chat not used by any prior artifact")
                    chain_depth[name] = chain_depth.get(item["source"], 0)
                    repairs += 1
                else:
                    latest = [v for v in seen.values() if v["page"] == page]
                    if not latest or latest[-1]["file"] != item.get("source"):
                        raise ValueError("Edit source must be the latest image on the same page")
                    if not parent["qa_passed"]:
                        raise ValueError("Reverse edit source must have passed QA")
                    if not isinstance(item.get("level"), int) or item["level"] != parent["level"] - 1:
                        raise ValueError("Reverse edit must remove exactly one reveal level")
                    chain_depth[name] = chain_depth[item["source"]] + 1
            seen[name] = dict(item, size=size)
            hashes.add(actual)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            errors.append(str(exc))
    if mode == "acceptance":
        if final_pages != set(selected):
            errors.append("Selected pages do not all have finals")
        if max(chain_depth.values(), default=0) < 2:
            errors.append("Missing two consecutive reverse edits")
        if not repairs:
            errors.append("Missing clean-chat repair")

    expected = run.get("expected_outputs")
    missing, selected_files = [], []
    if mode == "production":
        if not isinstance(expected, list) or not expected:
            errors.append("Production requires expected_outputs")
            expected = []
        expected_seen = set()
        expected_pages = {}
        for output in expected:
            try:
                if not isinstance(output, dict):
                    raise ValueError("Expected output must be an object")
                name, page = output["file"], output["page"]
                if not isinstance(name, str) or Path(name).suffix.lower() != ".png":
                    raise ValueError("Invalid expected PNG filename: " + str(name))
                contained(root, name)
                if page not in selected or name in expected_seen:
                    raise ValueError("Invalid or duplicate expected output: " + name)
                expected_seen.add(name)
                expected_pages[name] = page
                candidates = [v for v in seen.values() if v["page"] == page and _artifact_target(v) == name and v["qa_passed"]]
                if not candidates:
                    missing.append(name)
                else:
                    selected_files.append(candidates[-1]["file"])
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(str(exc))
        for item in seen.values():
            if item["stage"] == "repair":
                target = _artifact_target(item)
                if target not in expected_pages or expected_pages[target] != item["page"]:
                    errors.append("Repair target must be an expected output on the same page: " + item["file"])
    else:
        selected_files = [v["file"] for v in seen.values() if v["qa_passed"]]
        if any(not item["qa_passed"] for item in seen.values()):
            missing.extend(item["file"] for item in seen.values() if not item["qa_passed"])
    complete = not errors and not missing
    return {"mechanical_pass": not errors, "complete": complete, "missing": missing,
            "selected_files": selected_files, "errors": errors,
            "planned_pages": len(pages), "selected_pages": len(set(selected)),
            "artifacts": len(seen), "generation_chats": len(chats),
            "visual_quality": "not_verified_by_this_script",
            "actual_ai_source_use": "requires_runtime_evidence_review",
            "token_usage": None}


def pack(root, run, destination, mode=None, allow_partial=False):
    result = check(root, run, mode=mode)
    if not result["mechanical_pass"]:
        raise ValueError("Packaging refused: " + "; ".join(result["errors"]))
    if not result["complete"] and not allow_partial:
        raise ValueError("Packaging refused: expected outputs are missing")
    names = {"run.json"}
    selected_names = set(result["selected_files"])
    for item in run.get("artifacts", []):
        if item["file"] in selected_names:
            names.update([item["file"], item["prompt"]])
    declared = run.get("deliverables", [])
    if not isinstance(declared, list):
        raise ValueError("Deliverables must be a list")
    deliverables = list(declared)
    for output in run.get("expected_outputs", []) if isinstance(run.get("expected_outputs", []), list) else []:
        if not isinstance(output, dict):
            continue
        candidates = [item for item in run.get("artifacts", []) if isinstance(item, dict) and
                      item.get("page") == output.get("page") and _artifact_target(item) == output.get("file") and
                      item.get("qa_passed") is True]
        if candidates:
            selected = candidates[-1]
            deliverables.extend([selected.get("file"), selected.get("prompt")])
    deliverables = list(dict.fromkeys(["run.json", *deliverables]))
    names.update(deliverables)
    allowed = {".png", ".md", ".txt", ".json"}
    artifact_paths = {
        contained(root, item["file"]): item["file"] in selected_names
        for item in run.get("artifacts", [])
    }
    input_paths = {contained(root, item["file"]) for item in run.get("inputs", [])}
    reserved_status = contained(root, "delivery-status.json")
    for name in names:
        if name in ("run.json", "delivery-status.json"):
            if name == "delivery-status.json":
                raise ValueError("delivery-status.json is reserved")
            continue
        path = contained(root, name)
        if path.suffix.lower() not in allowed or not path.is_file():
            raise ValueError("Unsupported deliverable: " + name)
        if path == reserved_status:
            raise ValueError("delivery-status.json is reserved")
        if name in deliverables and path.suffix.lower() == ".png":
            if path in artifact_paths and not artifact_paths[path] and path not in input_paths:
                raise ValueError("Cannot package a QA-failed or unselected artifact PNG: " + name)
    destination = Path(destination).resolve()
    if destination.exists() or destination in [contained(root, n) for n in names]:
        raise ValueError("Destination already exists or collides with an input")
    serialized_run = json.dumps(run, ensure_ascii=False, indent=2).encode("utf-8")
    if not result["complete"]:
        names.add("delivery-status.json")
    with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(names):
            if name == "run.json":
                archive.writestr(name, serialized_run)
            elif name == "delivery-status.json":
                archive.writestr(name, json.dumps({"complete": False, "missing": result["missing"]}, ensure_ascii=False, indent=2))
            else:
                archive.write(contained(root, name), name)
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP integrity failed")
        for name in names:
            if name == "run.json":
                expected_bytes = serialized_run
            elif name == "delivery-status.json":
                continue
            else:
                expected_bytes = contained(root, name).read_bytes()
            if hashlib.sha256(archive.read(name)).hexdigest() != hashlib.sha256(expected_bytes).hexdigest():
                raise ValueError("ZIP bytes mismatch")
    return result


def plan(run):
    groups = run.get("page_groups")
    if not isinstance(groups, list) or not groups:
        raise ValueError("page_groups must be a non-empty list")
    seen_pages, seen_outputs = set(), set()
    batches, batch = [], {"pages": [], "outputs": []}
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("page"), str) or not isinstance(group.get("outputs"), list):
            raise ValueError("Invalid page group")
        page, outputs = group["page"], group["outputs"]
        if page in seen_pages or len(outputs) > 10 or not outputs:
            raise ValueError("Duplicate page, empty group, or group exceeds 10 outputs")
        if any(not isinstance(name, str) or not name for name in outputs):
            raise ValueError("Invalid output name")
        if seen_outputs.intersection(outputs) or len(set(outputs)) != len(outputs):
            raise ValueError("Duplicate output")
        seen_pages.add(page)
        seen_outputs.update(outputs)
        if len(batch["outputs"]) + len(outputs) > 10:
            batches.append(batch)
            batch = {"pages": [], "outputs": []}
        batch["pages"].append(page)
        batch["outputs"].extend(outputs)
    if batch["outputs"]:
        batches.append(batch)
    return {"batches": [{"id": f"B{index:02d}", **item, "count": len(item["outputs"])} for index, item in enumerate(batches, 1)]}


def status(root, run):
    root = Path(root).resolve()
    batches = run.get("batches", [])
    if not isinstance(batches, list):
        raise ValueError("batches must be a list")
    result = []
    for batch in batches:
        if not isinstance(batch, dict):
            raise ValueError("Batch must be an object")
        state = batch.get("state")
        if state not in ("prepared", "submitting", "sent", "collected", "blocked"):
            raise ValueError("Invalid batch state")
        name = batch.get("prompt")
        prompt_ok, actual_hash = False, None
        if isinstance(name, str):
            try:
                path = contained(root, name)
                if path.is_file():
                    prompt_text = path.read_text(encoding="utf-8")
                    actual_hash = digest(path)
                    prompt_ok = bool(prompt_text.strip()) and bool(batch.get("prompt_sha256")) and actual_hash == batch.get("prompt_sha256")
            except (OSError, ValueError):
                pass
        outputs = batch.get("outputs", [])
        if not isinstance(outputs, list) or len(outputs) > 10:
            raise ValueError("Batch outputs must be a list of at most 10 files")
        missing = []
        for output in outputs:
            filename = output.get("file") if isinstance(output, dict) else output
            if not isinstance(filename, str):
                raise ValueError("Invalid batch output")
            try:
                if not contained(root, filename).is_file():
                    missing.append(filename)
            except ValueError:
                missing.append(filename)
        if not prompt_ok:
            missing.append(name if isinstance(name, str) else "prompt")
        all_outputs_exist = not missing and bool(outputs)
        if state == "prepared":
            action = "review_existing_outputs" if all_outputs_exist else ("ready_to_submit" if prompt_ok else "prepare_prompt_and_hash")
        elif state in ("submitting", "sent", "blocked"):
            action = "review_original_conversation_and_confirm_before_any_resend"
        else:
            action = "review_collected_outputs"
        result.append({"id": batch.get("id"), "state": state, "chat_url": batch.get("chat_url"),
                       "outputs": outputs, "prompt_valid": prompt_ok,
                       "missing": missing,
                       "action": action})
    return {"batches": result}


def register(root, run, record):
    """Validate and append one explicitly QA-reviewed artifact record."""
    if not isinstance(record, dict):
        raise ValueError("Record must be a JSON object")
    record = copy.deepcopy(record)
    required = ("file", "page", "stage", "chat_url", "package", "level", "prompt", "runtime_evidence", "qa_passed", "qa_note")
    if any(key not in record for key in required):
        raise ValueError("Record is missing required metadata")
    if not isinstance(record["qa_passed"], bool) or not isinstance(record["qa_note"], str) or not record["qa_note"].strip():
        raise ValueError("qa_passed must be a boolean and qa_note must be non-empty")
    filename = record["file"]
    if not isinstance(filename, str):
        raise ValueError("Invalid PNG filename")
    image = contained(root, filename)
    if not image.is_file():
        raise ValueError("PNG file does not exist: " + filename)
    png_size(image)
    record["sha256"] = digest(image)
    source = record.get("source")
    if source is not None:
        source_path = contained(root, source)
        if not source_path.is_file():
            raise ValueError("Source image does not exist: " + source)
        png_size(source_path)
        record["source_sha256"] = digest(source_path)
    candidate = copy.deepcopy(run)
    if not isinstance(candidate.get("artifacts", []), list):
        raise ValueError("Artifacts must be a list")
    candidate.setdefault("artifacts", []).append(record)
    checked = check(root, candidate)
    if checked["errors"]:
        raise ValueError("Registration refused: " + "; ".join(checked["errors"]))
    return candidate


def space(root, run):
    """Build a paste-ready Space manifest; this does not publish anything."""
    root = Path(root).resolve()
    checked = check(root, run)
    if not checked["mechanical_pass"]:
        raise ValueError("Space manifest refused: " + "; ".join(checked["errors"]))
    expected = run.get("expected_outputs", [])
    if not isinstance(expected, list):
        raise ValueError("expected_outputs must be a list")
    records = run.get("artifacts", [])
    if not isinstance(records, list):
        raise ValueError("artifacts must be a list")
    table = ["| 頁次 | 分鏡 | 狀態 | 完成 | 原圖 |", "|---|---|---|---|---|"]
    previews = ["## 圖檔（依頁次順序）", ""]
    adopted = {}
    for output in expected:
        if not isinstance(output, dict) or not isinstance(output.get("file"), str):
            raise ValueError("Invalid expected output")
        candidates = [item for item in records if isinstance(item, dict) and item.get("page") == output.get("page") and _artifact_target(item) == output["file"] and item.get("qa_passed") is True]
        if candidates:
            adopted[output["file"]] = candidates[-1]
    for output in expected:
        item = adopted.get(output["file"])
        reference = item.get("page_reference") if item else None
        if isinstance(reference, dict):
            reference = reference.get("uri")
        if not isinstance(reference, str) or not reference.startswith(("library-file:", "project-file:", "visualize:")):
            reference = None
        scene = output.get("storyboard", output.get("scene", output['file']))
        status_text = "完成" if item else "待製作"
        if reference:
            original = f"[PNG]({reference})"
            previews.extend([f"- [{item['file']}]({reference})", "",
                             f"![{item['file']}]({reference})", ""])
        else:
            original = "待上傳"
        table.append(f"| {output['page']} | {scene} | {status_text} | {'☑' if item else '☐'} | {original} |")
    valid_references = [item.get("page_reference") for item in adopted.values()
                        if isinstance(item.get("page_reference"), str) and
                        item["page_reference"].startswith(("library-file:", "project-file:", "visualize:"))]
    if not valid_references:
        reference_note = "頁面引用：待上傳（artifact 尚無有效 page_reference）"
    else:
        reference_note = "每張圖片使用 artifact.page_reference；請貼入 Space 並確認上傳完成。"
    markdown = f"## 完成清單（{len(expected)} 張）\n\n已完成 {len(adopted)} 張，待製作 {len(expected)-len(adopted)} 張。\n\n" + "\n".join(table) + "\n\n" + "\n".join(previews)
    return {"columns": ["頁次", "分鏡", "狀態", "完成", "原圖"], "markdown": markdown,
            "page_references": valid_references, "reference_note": reference_note, "published": False}


def preflight(root, run, limit=10 * 1024 * 1024):
    """Read-only delivery inventory and conservative whole-page ZIP grouping."""
    root = Path(root).resolve()
    deliverables = run.get("deliverables", [])
    if not isinstance(deliverables, list):
        raise ValueError("Deliverables must be a list")
    unsupported, missing, supported = [], [], []
    allowed = {".png", ".md", ".txt", ".json"}
    for name in deliverables:
        if not isinstance(name, str):
            unsupported.append(str(name)); continue
        path = contained(root, name)
        if path.suffix.lower() not in allowed:
            unsupported.append(name)
        elif not path.is_file():
            missing.append(name)
        else:
            supported.append(name)
    expected = run.get("expected_outputs", [])
    records = run.get("artifacts", [])
    by_target = {}
    for item in records if isinstance(records, list) else []:
        if isinstance(item, dict) and item.get("qa_passed") is True:
            by_target.setdefault((_artifact_target(item), item.get("page")), []).append(item)
    page_sizes, grouped_pages = [], {}
    for output in expected if isinstance(expected, list) else []:
        if not isinstance(output, dict):
            continue
        candidates = by_target.get((output.get("file"), output.get("page")), [])
        if not candidates:
            key = output.get("page")
            grouped_pages.setdefault(key, {"files": [], "bytes": 0, "missing": []})["missing"].append(output.get("file"))
            continue
        record = candidates[-1]
        paths = [contained(root, record["file"])]
        for p in paths:
            if not p.is_file():
                missing.append(record["file"])
        size = sum(p.stat().st_size for p in paths if p.is_file())
        page_sizes.append({"page": output["page"], "bytes": size, "files": [record["file"]]})
        group = grouped_pages.setdefault(output["page"], {"files": [], "bytes": 0, "missing": []})
        group["files"].append(record["file"])
        group["bytes"] += size
    groups, current = [], {"pages": [], "files": [], "selected_png_bytes": 0}
    for page, page_group in grouped_pages.items():
        files, size = page_group["files"], page_group["bytes"]
        # Include a conservative DEFLATE bound, two filename copies, per-file
        # headers/descriptors, and a fixed archive overhead.
        file_overhead = sum(((root / name).stat().st_size + 16383) // 16384 * 5
                            + 2 * len(name.encode("utf-8")) + 128 for name in files)
        overhead = file_overhead + 1024
        estimated = size + overhead
        if estimated > limit:
            if current["pages"]:
                groups.append(current); current = {"pages": [], "files": [], "selected_png_bytes": 0}
            groups.append({"pages": [page], "files": files, "selected_png_bytes": size,
                           "estimated_zip_bytes": estimated, "missing": page_group["missing"], "over_limit": True})
        else:
            if current["pages"] and current["estimated_zip_bytes"] + size + file_overhead > limit:
                groups.append(current); current = {"pages": [], "files": [], "selected_png_bytes": 0}
            current["pages"].append(page); current["files"].extend(files); current["selected_png_bytes"] += size
            current["estimated_zip_bytes"] = current.get("estimated_zip_bytes", 1024) + size + file_overhead
            current.setdefault("missing", []).extend(page_group["missing"])
    if current["pages"]:
        groups.append(current)
    return {"deliverables": supported, "unsupported": unsupported, "missing": sorted(set(missing)),
            "page_png_bytes": page_sizes, "zip_groups": groups, "group_limit_bytes": limit,
            "zip_created": False, "zip_estimate_note": "以原PNG bytes 加保守 ZIP 標頭估算；實際封裝後仍須量測。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "pack", "plan", "status", "register", "space", "preflight"])
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mode", choices=["production", "acceptance"])
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--record", type=Path, help="JSON file for register")
    args = parser.parse_args()
    run = json.loads(args.run.read_text(encoding="utf-8-sig"))
    if args.command == "pack" and not args.output:
        parser.error("pack requires --output")
    if args.command == "register" and not args.record:
        parser.error("register requires --record")
    if args.command == "pack":
        result = pack(args.run.parent, run, args.output, mode=args.mode, allow_partial=args.allow_partial)
    elif args.command == "check":
        result = check(args.run.parent, run, mode=args.mode)
    elif args.command == "plan":
        result = plan(run)
    elif args.command == "register":
        updated = register(args.run.parent, run, json.loads(args.record.read_text(encoding="utf-8-sig")))
        serialized = json.dumps(updated, ensure_ascii=False, indent=2) + "\n"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=args.run.parent,
                                             prefix=args.run.name + ".", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(serialized)
            if json.loads(temporary.read_text(encoding="utf-8-sig")) != updated:
                raise ValueError("Registration readback mismatch")
            os.replace(temporary, args.run)
            if json.loads(args.run.read_text(encoding="utf-8-sig")) != updated:
                raise ValueError("Registration file readback mismatch")
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        result = {"registered": True, "file": updated["artifacts"][-1]["file"], "sha256": updated["artifacts"][-1]["sha256"]}
    elif args.command == "space":
        result = space(args.run.parent, run)
    elif args.command == "preflight":
        result = preflight(args.run.parent, run)
    else:
        result = status(args.run.parent, run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "pack":
        return 0 if result.get("mechanical_pass", False) else 1
    return 0 if result.get("mechanical_pass", True) and result.get("complete", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
