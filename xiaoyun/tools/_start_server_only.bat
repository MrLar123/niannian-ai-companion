@echo off
cd /d D:\LunaAI\xiaoyun
set OLLAMA_MODELS=D:\OllamaModels
start "" /b "D:\LunaAI\app\AI-Vtuber-20240229\Miniconda3\pythonw.exe" xiaoyun_server.py 1>>srv_out.log 2>>srv_err.log
