-- Volume de um semestre numa faculdade: 12 cursos, 80 professores, 5.000 alunos, 1.600 aulas de ~70 presenças,
-- mais 1 palestra com TODOS os 5.000 alunos presentes, 25 mil tentativas recusadas e 60 mil linhas de auditoria.
INSERT INTO cursos (nome, ativo, departamento) SELECT 'Curso ' || g, true, 'Depto ' || (g % 4) FROM generate_series(1, 12) g;
INSERT INTO professores (ativo, coordenacao, aprovado, registro_funcional, departamento, nome, email, senha_hash, criado_em)
  SELECT true, g = 1, true, 'DOC-' || g, 'Depto', 'Professor ' || g, 'prof' || g || '@unicerrado.edu.br', 'pbkdf2$' || repeat('a', 32) || '$' || repeat('b', 64), now() FROM generate_series(1, 80) g;
INSERT INTO alunos (nome, email, matricula, curso_id, periodo, status, senha_hash, criado_em)
  SELECT 'Aluno ' || lpad(g::text, 5, '0') || ' da Silva', 'aluno' || g || '@alunos.unicerrado.edu.br', 'M' || lpad(g::text, 7, '0'), 1 + (g % 12), 1 + (g % 10),
         'ativo', 'pbkdf2$' || repeat('a', 32) || '$' || repeat('b', 64), now() FROM generate_series(1, 5000) g;
-- 1.600 aulas encerradas (20 por professor), cada uma de um curso/período
INSERT INTO aulas (professor_id, titulo, latitude, longitude, raio_metros, tipo, local_tipo, curso_id, periodo, disciplina, codigo_validacao, ativa, aberta_em, encerrada_em)
  SELECT 1 + (g % 80), 'Aula ' || g, -18.4157, -49.2183, 50, 'aula', 'dentro', 1 + (g % 12), 1 + (g % 10), 'Disc ' || (g % 30), 'K' || g, false,
         now() - (g || ' hours')::interval, now() - (g || ' hours')::interval + interval '1 hour' FROM generate_series(1, 1600) g;
-- presenças: cada aula recebe os ~70 alunos do seu curso/período (alunos com mesmo curso e período = id % 12 e id % 10)
INSERT INTO presencas (aula_id, aluno_id, latitude, longitude, distancia_m, precisao_m, device_id, fp_hash, ip, alertas, registrado_em)
  SELECT a.id, al.id, -18.4157, -49.2183, 10 + random() * 30, 12, 'dev-' || a.id || '-' || al.id, 'fp' || al.id, '200.1.' || (al.id % 250) || '.' || (al.id % 200),
         CASE WHEN random() < 0.01 THEN 'VPN/proxy detectado' END, a.aberta_em + (random() * 600 || ' seconds')::interval
  FROM aulas a JOIN alunos al ON al.curso_id = a.curso_id AND al.periodo = a.periodo
  WHERE random() < 0.9;
-- a palestra grande (todos os cursos): 5.000 presenças
INSERT INTO aulas (professor_id, titulo, latitude, longitude, raio_metros, tipo, local_tipo, curso_id, periodo, disciplina, codigo_validacao, ativa, aberta_em)
  VALUES (2, 'Palestra de abertura', -18.4157, -49.2183, 200, 'palestra', 'dentro', NULL, NULL, NULL, 'PAL1', true, now());
INSERT INTO presencas (aula_id, aluno_id, latitude, longitude, distancia_m, precisao_m, device_id, fp_hash, ip, alertas, registrado_em)
  SELECT (SELECT max(id) FROM aulas), id, -18.4157, -49.2183, 20, 12, 'dev-big-' || id, 'fp' || id, '200.1.1.1', NULL, now() + (id || ' milliseconds')::interval FROM alunos;
INSERT INTO tentativas (aula_id, aluno_id, motivo, detalhe, quantidade, criado_em, ultima_em)
  SELECT 1 + (g % 1600), 1 + (g % 5000), (ARRAY['fora_raio','vpn','gps_suspeito'])[1 + (g % 3)], 'x', 1, now(), now() - (g || ' seconds')::interval FROM generate_series(1, 25000) g
  ON CONFLICT DO NOTHING;
INSERT INTO logs_seguranca (usuario_id, acao, detalhe, data_hora, ip) SELECT 1 + (g % 80), 'exportar_chamada', 'aula=' || g, now() - (g || ' seconds')::interval, '177.1.1.1' FROM generate_series(1, 60000) g;
INSERT INTO matriculas_autorizadas (matricula, curso_id, utilizada) SELECT 'M' || lpad(g::text, 7, '0'), 1 + (g % 12), g <= 5000 FROM generate_series(1, 6000) g;
INSERT INTO notas_aluno (aluno_id, presenca_id, evento_titulo, evento_tipo, evento_disciplina, evento_em, texto, criado_em, atualizado_em)
  SELECT 1 + (g % 5000), g, 'Aula', 'aula', 'D', now(), repeat('anotação ', 30), now(), now() FROM generate_series(1, 20000) g;
ANALYZE;
SELECT 'alunos' t, count(*) FROM alunos UNION ALL SELECT 'aulas', count(*) FROM aulas UNION ALL SELECT 'presencas', count(*) FROM presencas
 UNION ALL SELECT 'tentativas', count(*) FROM tentativas UNION ALL SELECT 'logs', count(*) FROM logs_seguranca UNION ALL SELECT 'notas', count(*) FROM notas_aluno;
