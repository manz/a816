import os
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def zip_release(zip_file: ZipFile, release_path: Path):
    for root, _, files in os.walk(release_path):
        for file in files:
            zip_file.write(Path(root) / Path(file), file)


def main():
    with ZipFile("a816.zip", "w", ZIP_DEFLATED) as zip_file:
        zip_release(zip_file, Path("./build"))


if __name__ == "__main__":
    main()
