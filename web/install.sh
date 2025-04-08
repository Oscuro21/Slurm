#!/bin/bash
# Script de instalación para la aplicación web en Debian 12

# Actualizar el sistema
echo "Actualizando el sistema..."
sudo apt update
sudo apt upgrade -y

# Instalar Python 3, pip y el módulo de entornos virtuales
echo "Instalando Python3, pip y venv..."
sudo apt install -y python3 python3-pip python3-venv

# Crear el entorno virtual en la carpeta web (si no existe)
if [ ! -d "venv" ]; then
    echo "Creando el entorno virtual..."
    python3 -m venv venv
else
    echo "El entorno virtual ya existe."
fi

# Activar el entorno virtual e instalar dependencias
echo "Activando el entorno virtual e instalando dependencias..."
source venv/bin/activate
pip install --upgrade pip
pip install flask python-pam

echo "Instalación completada. Puedes iniciar la aplicación con ./start.sh."
