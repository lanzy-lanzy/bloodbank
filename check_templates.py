import django, os, glob, sys
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from django.template.loader import get_template

fails = 0
for f in sorted(glob.glob("templates/**/*.html", recursive=True)):
    name = f[10:].replace("\\", "/")
    try:
        get_template(name)
    except Exception as e:
        fails += 1
        print("FAIL", name, "->", str(e)[:200])
print("checked", len(glob.glob('templates/**/*.html', recursive=True)), "templates, failures:", fails)
sys.exit(1 if fails else 0)
