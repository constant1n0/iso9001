# Este archivo es parte de "ABSOLUT ISO9001".
#
# "ABSOLUT ISO9001" es software libre: puede redistribuirlo y/o modificarlo
# bajo los términos de la Licencia Pública General GNU publicada por la
# Free Software Foundation, ya sea la versión 3 de la Licencia o (a su
# elección) cualquier versión posterior.
#
# "ABSOLUT ISO9001" se distribuye con la esperanza de que sea útil,
# pero SIN NINGUNA GARANTÍA; incluso sin la garantía implícita de
# COMERCIABILIDAD o IDONEIDAD PARA UN PROPÓSITO PARTICULAR. Consulte la
# Licencia Pública General GNU para obtener más detalles.
#
# Debería haber recibido una copia de la Licencia Pública General GNU
# junto con este programa. En caso contrario, consulte <https://www.gnu.org/licenses/>.

import multiprocessing
import os

# Dirección y puerto
bind = os.environ.get('GUNICORN_BIND', '0.0.0.0:5000')

# Número de workers (recomendado: 2-4 x núcleos CPU)
workers = int(os.environ.get('GUNICORN_WORKERS', multiprocessing.cpu_count() * 2 + 1))

# Tipo de worker
worker_class = 'sync'

# Threads por worker
threads = int(os.environ.get('GUNICORN_THREADS', 2))

# Timeout para requests (segundos)
timeout = 120

# Keep-alive (segundos)
keepalive = 5

# Máximo de requests por worker antes de reiniciar (previene memory leaks)
max_requests = 1000
max_requests_jitter = 50

# Logging
accesslog = '/home/dcm/work/iso9001/logs/gunicorn-access.log'
errorlog = '/home/dcm/work/iso9001/logs/gunicorn-error.log'
loglevel = 'info'
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Proceso
daemon = False
pidfile = '/home/dcm/work/iso9001/gunicorn.pid'

# Seguridad
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190

# Graceful restart
graceful_timeout = 30

# Preload app para compartir memoria entre workers
preload_app = True

# Forward de headers de proxy
forwarded_allow_ips = '*'
proxy_allow_ips = '*'

# Hooks para logging
def on_starting(server):
    print("Iniciando servidor Gunicorn...")

def on_reload(server):
    print("Recargando servidor Gunicorn...")

def worker_abort(worker):
    print(f"Worker {worker.pid} abortado")
