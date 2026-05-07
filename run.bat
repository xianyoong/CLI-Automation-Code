@echo off
title .NET SDK Test Runner
echo ========================================
echo   .NET SDK Test Runner
echo ========================================
echo.

:: Check Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo ERROR: Python not found. Install Python 3.9+ from https://python.org
    pause
    exit /b 1
)

:: Check Node.js
where node >nul 2>nul
if %errorlevel% neq 0 (
    echo ERROR: Node.js not found. Install Node.js 18+ from https://nodejs.org
    pause
    exit /b 1
)

:: Install backend dependencies
echo [1/4] Installing Python dependencies...
cd backend
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo ERROR: Failed to install Python dependencies
    pause
    exit /b 1
)
cd ..

:: Install frontend dependencies
echo [2/4] Installing frontend dependencies...
cd frontend
if not exist "node_modules" (
    call npm install --silent
)
cd ..

:: Start backend
echo [3/4] Starting backend server...
start /b "Backend" cmd /c "cd backend && python app.py"

:: Wait for backend
timeout /t 2 >nul

:: Start frontend
echo [4/4] Starting frontend dev server...
start /b "Frontend" cmd /c "cd frontend && npm run dev"

echo.
echo ========================================
echo   App running at: http://localhost:3000
echo   API running at: http://localhost:5000
echo ========================================
echo   Press Ctrl+C to stop
echo.
pause
