-- Esquema do Guará Check no PostgreSQL, igual ao que o SQLAlchemy gera a partir dos modelos de app.py
-- (colunas, UNIQUEs, índices e chaves estrangeiras). Usado só nos testes de carga (F9).
DROP SCHEMA public CASCADE; CREATE SCHEMA public;

CREATE TABLE cursos (id SERIAL PRIMARY KEY, nome VARCHAR(100) NOT NULL UNIQUE, ativo BOOLEAN, departamento VARCHAR(100));
CREATE TABLE professores (
  id SERIAL PRIMARY KEY, ativo BOOLEAN, coordenacao BOOLEAN, aprovado BOOLEAN, registro_funcional VARCHAR(30), departamento VARCHAR(100),
  nome VARCHAR(120) NOT NULL, email VARCHAR(160) NOT NULL, senha_hash VARCHAR(200) NOT NULL, criado_em TIMESTAMPTZ);
CREATE UNIQUE INDEX ix_professores_email ON professores (email);
CREATE UNIQUE INDEX uq_professores_registro ON professores (registro_funcional);
CREATE TABLE alunos (
  id SERIAL PRIMARY KEY, nome VARCHAR(120) NOT NULL, email VARCHAR(160) NOT NULL, matricula VARCHAR(30),
  curso_id INTEGER REFERENCES cursos(id), periodo INTEGER, status VARCHAR(10), senha_hash VARCHAR(200) NOT NULL, criado_em TIMESTAMPTZ);
CREATE UNIQUE INDEX ix_alunos_email ON alunos (email);
CREATE INDEX ix_alunos_curso_id ON alunos (curso_id);
CREATE UNIQUE INDEX uq_alunos_matricula ON alunos (matricula);
CREATE TABLE aulas (
  id SERIAL PRIMARY KEY, professor_id INTEGER NOT NULL REFERENCES professores(id), titulo VARCHAR(140) NOT NULL,
  latitude FLOAT NOT NULL, longitude FLOAT NOT NULL, raio_metros FLOAT NOT NULL, tipo VARCHAR(20) NOT NULL, local_tipo VARCHAR(10) NOT NULL,
  local_descricao VARCHAR(160), curso_id INTEGER REFERENCES cursos(id), periodo INTEGER, disciplina VARCHAR(140), codigo_validacao VARCHAR(16),
  ativa BOOLEAN NOT NULL, aberta_em TIMESTAMPTZ NOT NULL, encerrada_em TIMESTAMPTZ);
CREATE INDEX ix_aulas_professor_id ON aulas (professor_id);
CREATE INDEX ix_aulas_ativa ON aulas (ativa);
CREATE UNIQUE INDEX uq_aula_ativa_prof ON aulas (professor_id) WHERE ativa;
CREATE TABLE presencas (
  id SERIAL PRIMARY KEY, aula_id INTEGER NOT NULL REFERENCES aulas(id) ON DELETE CASCADE, aluno_id INTEGER NOT NULL REFERENCES alunos(id),
  latitude FLOAT NOT NULL, longitude FLOAT NOT NULL, distancia_m FLOAT NOT NULL, precisao_m FLOAT, device_id VARCHAR(64), fp_hash VARCHAR(64),
  ip VARCHAR(64), alertas VARCHAR(200), registrado_em TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_presenca_aula_aluno UNIQUE (aula_id, aluno_id), CONSTRAINT uq_presenca_aula_device UNIQUE (aula_id, device_id));
CREATE INDEX ix_presencas_aluno_id ON presencas (aluno_id);
CREATE INDEX ix_presenca_aula_id_id ON presencas (aula_id, id);
CREATE TABLE tentativas (
  id SERIAL PRIMARY KEY, aula_id INTEGER NOT NULL REFERENCES aulas(id) ON DELETE CASCADE, aluno_id INTEGER NOT NULL REFERENCES alunos(id),
  motivo VARCHAR(20) NOT NULL, detalhe VARCHAR(200), latitude FLOAT, longitude FLOAT, distancia_m FLOAT, precisao_m FLOAT, ip VARCHAR(64),
  quantidade INTEGER NOT NULL, criado_em TIMESTAMPTZ NOT NULL, ultima_em TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_tentativa UNIQUE (aula_id, aluno_id, motivo));
CREATE INDEX ix_tentativas_aluno_id ON tentativas (aluno_id);
CREATE INDEX ix_tentativa_aula_ultima ON tentativas (aula_id, ultima_em);
CREATE TABLE notas_aluno (
  id SERIAL PRIMARY KEY, aluno_id INTEGER NOT NULL REFERENCES alunos(id) ON DELETE CASCADE, presenca_id INTEGER NOT NULL,
  evento_titulo VARCHAR(140) NOT NULL, evento_tipo VARCHAR(20), evento_disciplina VARCHAR(140), evento_em TIMESTAMPTZ NOT NULL, texto TEXT NOT NULL,
  criado_em TIMESTAMPTZ NOT NULL, atualizado_em TIMESTAMPTZ NOT NULL, CONSTRAINT uq_nota_aluno_presenca UNIQUE (aluno_id, presenca_id));
CREATE INDEX ix_notas_aluno_aluno_id ON notas_aluno (aluno_id);
CREATE TABLE matriculas_autorizadas (id SERIAL PRIMARY KEY, matricula VARCHAR(30) NOT NULL, curso_id INTEGER REFERENCES cursos(id), utilizada BOOLEAN NOT NULL);
CREATE UNIQUE INDEX ix_matriculas_autorizadas_matricula ON matriculas_autorizadas (matricula);
CREATE INDEX ix_matriculas_autorizadas_utilizada ON matriculas_autorizadas (utilizada);
CREATE TABLE logs_seguranca (id SERIAL PRIMARY KEY, usuario_id INTEGER, acao VARCHAR(40) NOT NULL, detalhe VARCHAR(300), data_hora TIMESTAMPTZ NOT NULL, ip VARCHAR(64));
CREATE INDEX ix_logs_seguranca_usuario_id ON logs_seguranca (usuario_id);
CREATE INDEX ix_logs_seguranca_data_hora ON logs_seguranca (data_hora);
