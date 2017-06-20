#!/bin/bash
set -x
set -e
# Please update these links carefully, some versions won't work under Wine
PYTHON_URL=https://www.python.org/ftp/python/3.4.4/python-3.4.4.msi
#PYTHON_URL=http://www.python.org/ftp/python/2.7.8/python-2.7.8.msi
#PYQT4_URL=http://sourceforge.net/projects/pyqt/files/PyQt4/PyQt-4.11.1/PyQt4-4.11.1-gpl-Py2.7-Qt4.8.6-x32.exe
PYWIN32_URL=http://netix.dl.sourceforge.net/project/pywin32/pywin32/Build%20220/pywin32-220.win32-py3.4.exe
#PYWIN32_URL=http://sourceforge.net/projects/pywin32/files/pywin32/Build%20219/pywin32-220.win32-py3.4.exe/download
PYINSTALLER_URL=https://pypi.python.org/packages/source/P/PyInstaller/PyInstaller-3.1.1.zip
SETUPTOOLS_URL=https://pypi.python.org/packages/2.7/s/setuptools/setuptools-0.6c11.win32-py2.7.exe


## These settings probably don't need change
export WINEPREFIX=/opt/wine64
#export WINEARCH='win32'

PYHOME=c:/python34
PYTHON="wine $PYHOME/python.exe -OO -B"

# Let's begin!
cd `dirname $0`

# Clean up Wine environment
echo "Cleaning $WINEPREFIX"
rm -rf $WINEPREFIX
echo "done"

wine 'wineboot'

echo "Cleaning tmp"
rm -rf tmp
mkdir -p tmp
echo "done"

cd tmp

# Install Python
wget -O python.msi "$PYTHON_URL"
wine msiexec /q /i python.msi

# Install PyWin32
wget -O pywin32.exe "$PYWIN32_URL"
wine pywin32.exe

# Install PyQt4
#wget -O PyQt.exe "$PYQT4_URL"
#wine PyQt.exe

# Install pyinstaller
wget -O pyinstaller.zip "$PYINSTALLER_URL"
unzip pyinstaller.zip
mv PyInstaller-3.1.1 $WINEPREFIX/drive_c/pyinstaller

# Install setuptools
wget -O setuptools.exe "$SETUPTOOLS_URL"
wine setuptools.exe

# add dlls needed for pyinstaller:
#cp $WINEPREFIX/drive_c/windows/system32/msvcp90.dll $WINEPREFIX/drive_c/Python35/
#cp $WINEPREFIX/drive_c/windows/system32/msvcm90.dll $WINEPREFIX/drive_c/Python35/
