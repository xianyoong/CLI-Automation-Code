@echo off
title .NET SDK Test Runner (Production)
echo Building and starting in production mode...

:: Build frontend
cd frontend
call npm run build
cd ..

:: Copy build to backend static
if exist "backend\static" rmdir /s /q "backend\static"
xcopy /s /e /q frontend\dist backend\static\

:: Start backend (serves both API and static frontend)
echo.
echo App running at: http://localhost:5000
echo.
cd backend
python app.py
