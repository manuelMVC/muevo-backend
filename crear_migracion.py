content = open('C:/Users/manuel_perez/Downloads/001_initial.py').read()
open('C:/muevo-backend/alembic/versions/001_initial.py', 'w').write(content)
print('Copiado OK:', len(content), 'bytes')