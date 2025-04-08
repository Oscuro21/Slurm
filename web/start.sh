#!/bin/bash
# Script para iniciar la aplicación web

echo "Activando el entorno virtual..."
source venv/bin/activate

echo "Iniciando la aplicación web..."
python3 app.py
