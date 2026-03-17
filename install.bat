@echo off
echo Creating Python virtual environment...
python -m venv venv
echo.
echo Activating virtual environment and installing dependencies...
call venv\Scripts\activate.bat
pip install -U pip
pip install -r requirements.txt
echo.
echo Installing Playwright browsers...
playwright install chromium
echo.
echo Installation complete! You can now run start.bat to start the browser server.
pause
