import os
from zipfile import ZipFile, ZIP_DEFLATED


def zip_release(zip_file: ZipFile):
    # ziph is zipfile handle
    for root, dirs, files in os.walk("build"):
        for file in files:
            zip_file.write(os.path.join(root, file), file)


if __name__ == "__main__":
    with ZipFile("a816.zip", "w", ZIP_DEFLATED) as zip_file:
        zip_release(zip_file)
