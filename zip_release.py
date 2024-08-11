from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    with ZipFile("a816.zip", "w", ZIP_DEFLATED) as zip_file:
        zip_file.write(Path("./x816.exe"))


if __name__ == "__main__":
    main()
