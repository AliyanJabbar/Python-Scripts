from pathlib import Path
from PIL import Image # install pillow library
from io import BytesIO
from datetime import datetime
import csv
import sys

# ============================================================
# SETTINGS
# ============================================================

EXTENSIONS = {".jpg", ".jpeg", ".png"}

# Highest quality first.
# A lower quality is selected only when needed to produce
# a file smaller than the original JPEG.
JPEG_QUALITIES = [92, 90, 88, 85, 82, 80, 78, 75]

# ============================================================
# SELECT SOURCE FOLDER
# ============================================================

def choose_source_folder():
    # Optional: allow the folder to be supplied as a command argument.
    if len(sys.argv) > 1:
        folder = Path(sys.argv[1]).expanduser().resolve()
    else:
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)

            selected = filedialog.askdirectory(
                title="Select the folder containing your images",
                mustexist=True,
            )

            root.destroy()

            if not selected:
                raise SystemExit("No folder selected. Nothing changed.")

            folder = Path(selected).resolve()

        except SystemExit:
            raise
        except Exception as error:
            raise SystemExit(
                "Could not open the folder picker. "
                "Run the script with a folder path instead.\n"
                f"Details: {error}"
            )

    if not folder.is_dir():
        raise SystemExit(f"Folder does not exist: {folder}")

    return folder


def create_output_folder(source):
    # Never delete an older output folder or its files.
    base = source / "optimized_webp"

    if not base.exists():
        base.mkdir()
        return base

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = source / f"optimized_webp_{timestamp}"
    counter = 2

    while candidate.exists():
        candidate = source / f"optimized_webp_{timestamp}_{counter}"
        counter += 1

    candidate.mkdir()
    return candidate


# ============================================================
# IMAGE PROCESSING
# ============================================================

def optimize_image(path, destination):
    is_png = path.suffix.lower() == ".png"
    original_size = path.stat().st_size

    with Image.open(path) as original:
        original.load()

        # WebP cannot retain arbitrary high-bit-depth PNG data.
        # Reject these modes rather than silently reducing precision.
        if is_png and (
            original.mode in {"I", "F"}
            or original.mode.startswith("I;16")
        ):
            raise ValueError(
                f"Unsupported high-bit-depth PNG mode: {original.mode}"
            )

        has_alpha = (
            "A" in original.getbands()
            or "transparency" in original.info
        )

        target_mode = "RGBA" if has_alpha else "RGB"
        converted = original.convert(target_mode)

        metadata = {}

        if original.info.get("icc_profile"):
            metadata["icc_profile"] = original.info["icc_profile"]

        if original.info.get("exif"):
            metadata["exif"] = original.info["exif"]

        if is_png:
            # Lossless WebP: preserve decoded RGB/RGBA pixels,
            # including transparent pixels.
            buffer = BytesIO()

            converted.save(
                buffer,
                format="WEBP",
                lossless=True,
                method=6,
                exact=True,
                **metadata,
            )

            encoded = buffer.getvalue()
            chosen_quality = "LOSSLESS"

        else:
            # JPEG is already lossy. Find the highest tested
            # WebP quality that creates a smaller file.
            encoded = None
            chosen_quality = None

            for quality in JPEG_QUALITIES:
                buffer = BytesIO()

                converted.save(
                    buffer,
                    format="WEBP",
                    quality=quality,
                    method=6,
                    **metadata,
                )

                candidate = buffer.getvalue()

                if len(candidate) < original_size:
                    encoded = candidate
                    chosen_quality = quality
                    break

            # If no tested setting is smaller, preserve a high-quality
            # WebP at quality 92 and report its actual resulting size.
            if encoded is None:
                buffer = BytesIO()

                converted.save(
                    buffer,
                    format="WEBP",
                    quality=92,
                    method=6,
                    **metadata,
                )

                encoded = buffer.getvalue()
                chosen_quality = 92

    # Write the completed image.
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(encoded)

    # Reopen and verify the output.
    with Image.open(destination) as check:
        check.load()

        decoded_ok = (
            check.size == converted.size
            and check.mode == converted.mode
        )

        pixel_exact = (
            decoded_ok
            and check.tobytes() == converted.tobytes()
        )

    # PNG outputs must pass exact pixel verification.
    if not decoded_ok or (is_png and not pixel_exact):
        destination.unlink(missing_ok=True)
        raise ValueError("Image verification failed")

    output_size = destination.stat().st_size
    saved_bytes = original_size - output_size

    return {
        "width": converted.width,
        "height": converted.height,
        "original_bytes": original_size,
        "webp_bytes": output_size,
        "saved_bytes": saved_bytes,
        "saved_percent": round(
            saved_bytes / original_size * 100, 2
        ) if original_size else 0,
        "quality": chosen_quality,
        "verified": (
            "PIXEL-EXACT"
            if pixel_exact
            else "DECODED; JPEG LOSSY"
        ),
        "status": "Smaller" if saved_bytes > 0 else "Not smaller",
    }


# ============================================================
# MAIN PROGRAM
# ============================================================

def main():
    source = choose_source_folder()

    # Process files directly inside the selected folder.
    # Subfolders are not scanned.
    files = sorted(
        [
            p for p in source.iterdir()
            if p.is_file() and p.suffix.lower() in EXTENSIONS
        ],
        key=lambda p: p.name.lower(),
    )

    if not files:
        raise SystemExit(
            "No JPG, JPEG, or PNG images found directly in:\n"
            f"{source}"
        )

    output = create_output_folder(source)
    report_path = output / "webp_optimization_report.csv"

    # Handle same-name files with different extensions.
    stem_counts = {}

    for path in files:
        key = path.stem.casefold()
        stem_counts[key] = stem_counts.get(key, 0) + 1

    results = []
    total_input_bytes = 0
    successful_original_bytes = 0
    total_output_bytes = 0
    successes = 0
    failures = 0
    not_smaller = 0

    print(f"Source folder: {source}")
    print(f"Images found: {len(files)}")
    print(f"Output folder: {output}")
    print("Original images will not be modified.\n")

    for index, path in enumerate(files, start=1):
        before = None
        destination = None

        if stem_counts[path.stem.casefold()] > 1:
            output_name = (
                f"{path.stem}__{path.suffix[1:].lower()}.webp"
            )
        else:
            output_name = f"{path.stem}.webp"

        destination = output / output_name

        try:
            before = path.stat().st_size
            total_input_bytes += before

            result = optimize_image(path, destination)

            successes += 1
            successful_original_bytes += result["original_bytes"]
            total_output_bytes += result["webp_bytes"]

            if result["saved_bytes"] <= 0:
                not_smaller += 1

            results.append({
                "file": path.name,
                "output": destination.name,
                **result,
            })

            print(
                f"[{index}/{len(files)}] OK: {path.name} | "
                f"{result['original_bytes'] / 1024:.1f} KB -> "
                f"{result['webp_bytes'] / 1024:.1f} KB | "
                f"{result['saved_percent']:+.1f}% | "
                f"Quality: {result['quality']}"
            )

        except Exception as error:
            failures += 1

            if destination is not None:
                destination.unlink(missing_ok=True)

            print(
                f"[{index}/{len(files)}] FAILED: "
                f"{path.name} | {error}"
            )

            results.append({
                "file": path.name,
                "output": "",
                "width": "",
                "height": "",
                "original_bytes": before if before is not None else "",
                "webp_bytes": "",
                "saved_bytes": "",
                "saved_percent": "",
                "quality": "",
                "verified": "NO",
                "status": f"FAILED: {error}",
            })

    # Save the CSV report alongside the converted images.
    fields = [
        "file",
        "output",
        "width",
        "height",
        "original_bytes",
        "webp_bytes",
        "saved_bytes",
        "saved_percent",
        "quality",
        "verified",
        "status",
    ]

    with report_path.open(
        "w", newline="", encoding="utf-8-sig"
    ) as report:
        writer = csv.DictWriter(report, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    # Final summary.
    print("\n" + "=" * 52)
    print("OPTIMIZATION SUMMARY")
    print("=" * 52)

    print(f"Images found:                  {len(files)}")
    print(f"Successful conversions:        {successes}")
    print(f"Failures:                      {failures}")
    print(f"Files not made smaller:        {not_smaller}")

    print(
        f"Original size of all readable inputs: "
        f"{total_input_bytes / (1024 * 1024):.2f} MiB"
    )

    print(
        f"Original size of successful inputs:   "
        f"{successful_original_bytes / (1024 * 1024):.2f} MiB"
    )

    print(
        f"Output WebP size:                     "
        f"{total_output_bytes / (1024 * 1024):.2f} MiB"
    )

    if successful_original_bytes:
        saved = successful_original_bytes - total_output_bytes

        print(
            f"Net savings on successful files:      "
            f"{saved / (1024 * 1024):.2f} MiB"
        )

        print(
            f"Overall reduction on successful files: "
            f"{saved / successful_original_bytes * 100:.2f}%"
        )

    print(f"\nImages folder: {output}")
    print(f"CSV report:    {report_path}")
    print("\nOriginal source files have not been modified.")


if __name__ == "__main__":
    main()
