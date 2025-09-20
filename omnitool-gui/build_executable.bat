@echo off
echo Building OmniParser GUI executable...

REM Check if PyInstaller is installed
pip show pyinstaller >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Installing PyInstaller...
    pip install pyinstaller
    if %ERRORLEVEL% NEQ 0 (
        echo Failed to install PyInstaller. Please check your Python installation.
        exit /b 1
    )
)

REM Create the executable
echo Creating executable with PyInstaller...
pyinstaller --onefile --windowed --add-data "cursor.png;." --icon=NONE --name=OmniParserGUI app.py

if %ERRORLEVEL% NEQ 0 (
    echo Failed to create executable.
    exit /b 1
) else (
    echo.
    echo Build completed successfully!
    echo Executable is located in the 'dist' folder.
    echo.
)

REM Copy required files to the dist directory
echo Copying main.py to dist directory...
copy main.py dist\main.py

echo.
echo Setup complete. You can now run OmniParserGUI.exe from the dist folder.
echo.