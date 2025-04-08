import os
import subprocess
import tempfile
from functools import wraps
from flask import Flask, request, redirect, url_for, flash, get_flashed_messages, session
import pam  # Asegúrate de tener instalado python3-pam o un paquete equivalente

app = Flask(__name__)
app.secret_key = 'mi_clave_secreta'  # Cambia esto por una clave segura

# Diccionario global para mapear JOBID a usuario de la interfaz
JOB_MAP = {}

# Prevenir cacheo en el navegador
@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, public, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# Decorador para rutas protegidas
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            flash("Necesitas iniciar sesión.")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

#############################################
# Funciones para renderizar HTML (cabecera, pie y menú)
#############################################

def render_header(title):
    return f'''
    <html>
      <head>
        <meta charset="utf-8">
        <title>{title}</title>
        <style>
          body {{
            font-family: Arial, sans-serif;
            margin: 20px;
            background-color: #f8f8f8;
          }}
          nav {{
            background-color: #333;
            padding: 10px;
            text-align: center;
            margin-bottom: 20px;
          }}
          nav a {{
            color: #f8f8f8;
            margin: 0 15px;
            text-decoration: none;
            font-weight: bold;
          }}
          nav a:hover {{
            text-decoration: underline;
          }}
          .container {{
            background-color: #fff;
            padding: 20px;
            border-radius: 5px;
            box-shadow: 0 0 10px rgba(0,0,0,0.1);
          }}
          table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
          }}
          table, th, td {{
            border: 1px solid #ddd;
          }}
          th, td {{
            padding: 8px;
            text-align: center;
          }}
          th {{
            background-color: #f2f2f2;
          }}
          .flash {{
            color: green;
            font-weight: bold;
            margin-bottom: 10px;
          }}
          .button {{
            background-color: #ff4d4d;
            color: white;
            padding: 5px 10px;
            border: none;
            border-radius: 3px;
            cursor: pointer;
            text-decoration: none;
          }}
          .button:hover {{
            background-color: #e60000;
          }}
          input[type="text"], input[type="password"], textarea, select {{
            width: 100%;
            padding: 8px;
            margin: 4px 0;
            box-sizing: border-box;
          }}
          input[type="submit"] {{
            background-color: #4CAF50;
            color: white;
            border: none;
            padding: 10px 20px;
            cursor: pointer;
          }}
        </style>
      </head>
      <body>
        {nav_bar()}
        <div class="container">
    '''

def render_footer():
    return '''
        </div>
      </body>
    </html>
    '''

def nav_bar():
    if 'username' in session:
        user = session['username']
        login_links = f'<span style="color: white;">Bienvenido, {user}</span> | <a href="/logout">Logout</a>'
    else:
        login_links = '<a href="/login">Login</a>'
    return f'''
    <nav>
      <a href="/">Inicio</a>
      <a href="/submit">Enviar Trabajo</a>
      <a href="/template">Template</a>
      <a href="/nodes">Estado de Nodos</a>
      <a href="/cancel_jobs">Cancelar Trabajos</a>
      {login_links}
    </nav>
    '''

#############################################
# Funciones para modificar el script de Slurm
#############################################

def insertar_job_name(job_script, job_name):
    """
    Inserta (o actualiza) la directiva #SBATCH --job-name justo después del shebang.
    """
    lines = job_script.split("\n")
    new_lines = []
    inserted = False
    for line in lines:
        if line.startswith("#SBATCH --job-name="):
            continue  # se elimina la línea existente
        if not inserted and line.startswith("#!"):
            new_lines.append(line)
            new_lines.append(f"#SBATCH --job-name={job_name}")
            inserted = True
        else:
            new_lines.append(line)
    if not inserted:
        new_lines.insert(0, f"#SBATCH --job-name={job_name}")
    return "\n".join(new_lines)

def ensure_chdir_directive(job_script):
    """
    Asegura que el script tenga la directiva --chdir (usando /tmp).
    """
    lines = job_script.split("\n")
    for line in lines:
        if line.startswith("#SBATCH --chdir"):
            return job_script
    new_lines = []
    inserted = False
    for line in lines:
        if not inserted and line.startswith("#!"):
            new_lines.append(line)
            new_lines.append("#SBATCH --chdir=/tmp")
            inserted = True
        else:
            new_lines.append(line)
    if not inserted:
        new_lines.insert(0, "#SBATCH --chdir=/tmp")
    return "\n".join(new_lines)

#############################################
# RUTAS DE AUTENTICACIÓN (usando PAM)
#############################################

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password')
        p = pam.pam()
        if p.authenticate(username, password):
            session['username'] = username
            flash("Login exitoso.")
            return redirect(url_for('index'))
        else:
            flash("Login fallido. Revisa tus credenciales.")
            return redirect(url_for('login'))
    page = render_header("Login")
    page += '''
      <h2>Login</h2>
      <form method="post" autocomplete="off">
         <label>Usuario:</label><br>
         <input type="text" name="username" autocomplete="off"><br>
         <label>Contraseña:</label><br>
         <input type="password" name="password" autocomplete="off"><br><br>
         <input type="submit" value="Login">
      </form>
    '''
    page += render_footer()
    return page

@app.route('/logout')
def logout():
    session.pop('username', None)
    flash("Has cerrado sesión.")
    return redirect(url_for('login'))

#############################################
# RUTAS DEL SISTEMA SLURM Y REGISTRO DE TRABAJOS
#############################################

@app.route('/')
@login_required
def index():
    """
    Muestra una tabla con información completa de los trabajos enviados vía la interfaz.
    Columnas: JOBID, Partition, Name, State, Time, Nodes, NodeList, InterfaceUser.
    Ahora, cualquier usuario (incluidos los no-root) verá TODOS los trabajos registrados.
    """
    try:
        squeue_output = subprocess.check_output(
            ['squeue', '-h', '-o', '%i,%P,%j,%t,%M,%D,%R']
        ).decode('utf-8')
    except Exception as e:
        squeue_output = f"Error ejecutando squeue: {e}"
    
    rows = []
    if "Error ejecutando squeue:" not in squeue_output:
        for line in squeue_output.strip().splitlines():
            fields = line.split(',')
            if len(fields) >= 7:
                jobid     = fields[0]
                partition = fields[1]
                jname     = fields[2]
                state     = fields[3]
                time_used = fields[4]
                nodes     = fields[5]
                nodelist  = fields[6]
                if jobid in JOB_MAP:
                    iface_user = JOB_MAP[jobid]
                    # Se eliminó el filtro por usuario para que cualquier usuario vea todos los trabajos.
                    rows.append((jobid, partition, jname, state, time_used, nodes, nodelist, iface_user))
    
    messages = "<br>".join(get_flashed_messages())
    page = render_header("Trabajos en Cola")
    page += f'<div class="flash">{messages}</div>'
    page += '<h2>Trabajos en Cola</h2>'
    
    if rows:
        page += '''
        <table>
          <tr>
            <th>JOBID</th>
            <th>Partition</th>
            <th>Name</th>
            <th>State</th>
            <th>Time</th>
            <th>Nodes</th>
            <th>NodeList</th>
            <th>InterfaceUser</th>
          </tr>
        '''
        for r in rows:
            jobid, partition, jname, state, time_used, nodes, nodelist, iface_user = r
            page += f'<tr><td>{jobid}</td><td>{partition}</td><td>{jname}</td><td>{state}</td>'
            page += f'<td>{time_used}</td><td>{nodes}</td><td>{nodelist}</td><td>{iface_user}</td></tr>'
        page += '</table>'
    else:
        page += '<p>No hay trabajos registrados.</p>'
    
    page += render_footer()
    return page

@app.route('/submit', methods=['GET', 'POST'])
@login_required
def submit_job():
    if request.method == 'POST':
        job_script = request.form.get('job_script')
        job_name   = request.form.get('job_name', '').strip()
        if job_script:
            # Convertir saltos de línea DOS a UNIX para evitar errores en sbatch
            job_script = job_script.replace("\r\n", "\n")
            if job_name:
                job_script = insertar_job_name(job_script, job_name)
            job_script = ensure_chdir_directive(job_script)
            try:
                temp = tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.sh', dir='/tmp')
                temp.write(job_script)
                temp.flush()
                temp.close()
                os.chmod(temp.name, 0o755)
                output = subprocess.check_output(
                    ['sbatch', temp.name],
                    stderr=subprocess.STDOUT
                ).decode('utf-8')
                flash(f"Trabajo enviado exitosamente: {output}")
                job_id = output.strip().split()[-1]
                JOB_MAP[job_id] = session['username']
            except subprocess.CalledProcessError as e:
                flash(f"Error al enviar el trabajo: {e.output.decode('utf-8')}")
        else:
            flash("No se proporcionó script para el trabajo.")
        return redirect(url_for('index'))
    
    messages = "<br>".join(get_flashed_messages())
    page = render_header("Enviar Trabajo")
    page += f'<div class="flash">{messages}</div>'
    page += '''
      <h2>Enviar Trabajo</h2>
      <form method="post" autocomplete="off">
          <label>Nombre del trabajo:</label><br>
          <input type="text" name="job_name" placeholder="Ej. mi_trabajo" autocomplete="off"><br><br>
          <label>Script del trabajo:</label><br>
          <textarea name="job_script" cols="80" rows="10" placeholder="Escribe aquí el script para el trabajo..." autocomplete="off"></textarea><br>
          <input type="submit" value="Enviar Trabajo">
      </form>
    '''
    page += render_footer()
    return page

@app.route('/nodes')
@login_required
def nodes():
    try:
        sinfo_output = subprocess.check_output(['sinfo']).decode('utf-8')
    except Exception as e:
        sinfo_output = f"Error ejecutando sinfo: {e}"
    page = render_header("Estado de Nodos")
    page += f"<h2>Estado de los Nodos</h2><pre>{sinfo_output}</pre>"
    page += render_footer()
    return page

@app.route('/cancel_jobs')
@login_required
def cancel_jobs():
    username = session['username']
    show_all = False
    if username == 'root':
        show_all = (request.args.get('all') == 'on')
    try:
        output = subprocess.check_output(
            ['squeue', '-h', '-o', '%i,%P,%j,%t,%M,%D,%R']
        ).decode('utf-8')
    except Exception as e:
        output = ""
        flash(f"Error obteniendo lista de trabajos: {e}")
    
    rows = []
    if output:
        for line in output.strip().splitlines():
            fields = line.split(',')
            if len(fields) >= 7:
                jobid     = fields[0]
                partition = fields[1]
                jname     = fields[2]
                state     = fields[3]
                time_used = fields[4]
                nodes     = fields[5]
                nodelist  = fields[6]
                if jobid in JOB_MAP:
                    iface_user = JOB_MAP[jobid]
                    if username == 'root':
                        if show_all or iface_user == username:
                            rows.append((jobid, partition, jname, state, time_used, nodes, nodelist, iface_user))
                    else:
                        if iface_user == username:
                            rows.append((jobid, partition, jname, state, time_used, nodes, nodelist, iface_user))
    
    page = render_header("Cancelar Trabajos")
    page += '<h2>Cancelar Trabajos</h2>'
    if username == 'root':
        form_html = '''
        <form method="get" style="margin-bottom:20px;">
            <label>
                <input type="checkbox" name="all" value="on" {checked}> Mostrar trabajos de todos los usuarios
            </label>
            <input type="submit" value="Aplicar">
        </form>
        '''
        checked_attr = "checked" if show_all else ""
        page += form_html.format(checked=checked_attr)
    
    messages = "<br>".join(get_flashed_messages())
    page += f'<div class="flash">{messages}</div>'
    
    if rows:
        page += '''
        <table>
          <tr>
            <th>JOBID</th>
            <th>Partition</th>
            <th>Name</th>
            <th>State</th>
            <th>Time</th>
            <th>Nodes</th>
            <th>NodeList</th>
            <th>InterfaceUser</th>
            <th>Acción</th>
          </tr>
        '''
        for r in rows:
            jobid, partition, jname, state, time_used, nodes, nodelist, iface_user = r
            page += '<tr>'
            page += f'<td>{jobid}</td><td>{partition}</td><td>{jname}</td><td>{state}</td>'
            page += f'<td>{time_used}</td><td>{nodes}</td><td>{nodelist}</td><td>{iface_user}</td>'
            page += f'<td><a class="button" href="/cancel/{jobid}">Cancelar</a></td>'
            page += '</tr>'
        page += '</table>'
    else:
        page += '<p>No hay trabajos registrados.</p>'
    
    page += render_footer()
    return page

@app.route('/cancel/<jobid>')
@login_required
def cancel(jobid):
    username = session['username']
    if jobid not in JOB_MAP:
        flash("El trabajo no fue enviado vía la interfaz o ya se ha eliminado.")
        return redirect(url_for('cancel_jobs'))
    iface_user = JOB_MAP[jobid]
    if username != 'root' and iface_user != username:
        flash("No tienes permiso para cancelar este trabajo (no es tuyo).")
        return redirect(url_for('cancel_jobs'))
    try:
        subprocess.run(['scancel', jobid], check=True)
        flash(f"Trabajo {jobid} cancelado exitosamente.")
        JOB_MAP.pop(jobid, None)
    except subprocess.CalledProcessError as e:
        flash(f"Error al cancelar el trabajo {jobid}: {e}")
    return redirect(url_for('cancel_jobs'))

#############################################
# NUEVA RUTA: TEMPLATE
#############################################

@app.route('/template')
@login_required
def template():
    messages = "<br>".join(get_flashed_messages())
    page = render_header("Template de Trabajo")
    page += f'<div class="flash">{messages}</div>'
    page += '''
    <h2>Generador de Script de Trabajo</h2>
    <form method="post" action="/submit" autocomplete="off">
      <label>Nombre del trabajo:</label><br>
      <input type="text" id="job_name" name="job_name" oninput="updatePreview()" autocomplete="off"><br><br>
      
      <label>Tiempo (HH:MM:SS):</label><br>
      <select id="time" name="time" oninput="updatePreview()">
        <option value="">-- Seleccione --</option>
        <option value="00:10:00">00:10:00</option>
        <option value="00:30:00">00:30:00</option>
        <option value="01:00:00">01:00:00</option>
        <option value="02:00:00">02:00:00</option>
        <option value="04:00:00">04:00:00</option>
        <option value="08:00:00">08:00:00</option>
      </select><br><br>
      
      <label>Nodos:</label><br>
      <select id="nodes" name="nodes" oninput="updatePreview()">
        <option value="1" selected>1</option>
        <option value="2">2</option>
        <option value="4">4</option>
        <option value="8">8</option>
      </select><br><br>
      
      <label>Tareas por nodo:</label><br>
      <select id="ntasks" name="ntasks" oninput="updatePreview()">
        <option value="1" selected>1</option>
        <option value="2">2</option>
        <option value="4">4</option>
        <option value="8">8</option>
        <option value="16">16</option>
      </select><br><br>
      
      <label>Memoria (ej. 4G o personalizar):</label><br>
      <input type="text" id="mem" name="mem" placeholder="Ej: 4G" list="mem-options" oninput="updatePreview()" autocomplete="off">
      <datalist id="mem-options">
        <option value="1G">
        <option value="2G">
        <option value="4G">
        <option value="8G">
        <option value="16G">
        <option value="32G">
      </datalist><br><br>
      
      <label>Job Array (ej. 1-10):</label><br>
      <input type="text" id="array" name="array" placeholder="1-10" oninput="updatePreview()" autocomplete="off"><br><br>
      
      <label>Comandos o script adicional:</label><br>
      <textarea id="commands" name="commands" rows="5" cols="80" placeholder="Escribe aquí los comandos..." oninput="updatePreview()" autocomplete="off"></textarea><br><br>
      
      <label>Vista previa del script:</label><br>
      <textarea id="scriptPreview" name="job_script" rows="10" cols="80" readonly></textarea><br><br>
      
      <input type="submit" value="Enviar Trabajo">
    </form>
    <script>
    function updatePreview() {
      var jobName = document.getElementById("job_name").value;
      var time = document.getElementById("time").value;
      var nodes = document.getElementById("nodes").value;
      var ntasks = document.getElementById("ntasks").value;
      var mem = document.getElementById("mem").value;
      var array = document.getElementById("array").value;
      var commands = document.getElementById("commands").value;
      var script = "#!/bin/bash\\n";
      if (jobName) {
        script += "#SBATCH --job-name=" + jobName + "\\n";
      }
      if (time) {
        script += "#SBATCH --time=" + time + "\\n";
      }
      if (nodes) {
        script += "#SBATCH --nodes=" + nodes + "\\n";
      }
      if (ntasks) {
        script += "#SBATCH --ntasks-per-node=" + ntasks + "\\n";
      }
      if (mem) {
        script += "#SBATCH --mem=" + mem + "\\n";
      }
      if (array) {
        script += "#SBATCH --array=" + array + "\\n";
      }
      script += "#SBATCH --chdir=/tmp\\n\\n";
      script += commands;
      // Convertir saltos de línea DOS a UNIX
      script = script.replace(/\\r\\n/g, "\\n");
      document.getElementById("scriptPreview").value = script;
    }
    window.onload = updatePreview;
    </script>
    '''
    page += render_footer()
    return page

#############################################
# Arranque de la aplicación
#############################################

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
