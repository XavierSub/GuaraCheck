-- As consultas de cada rota, traduzidas do SQLAlchemy de app.py. Parâmetros: aluno 2500, aula comum 800, palestra grande = última aula.
\set big '(SELECT max(id) FROM aulas)'
\timing off
\echo '== Q01 login: busca por e-mail (unique)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT * FROM alunos WHERE email = 'aluno2500@alunos.unicerrado.edu.br';
\echo '== Q02 dados_conta / get do aluno (PK)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT * FROM alunos WHERE id = 2500;
\echo '== Q03 aulas ativas (join professor + curso, ativa, order id desc limit 500)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT a.*, p.nome, c.nome FROM aulas a JOIN professores p ON p.id = a.professor_id LEFT JOIN cursos c ON c.id = a.curso_id WHERE a.ativa ORDER BY a.id DESC LIMIT 500;
\echo '== Q04 aluno já registrou? (aluno_id + aula_id IN)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT aula_id FROM presencas WHERE aluno_id = 2500 AND aula_id IN (1601, 1600, 1599);
\echo '== Q05 check-in: duplicata (aula_id, aluno_id)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT distancia_m FROM presencas WHERE aula_id = 1601 AND aluno_id = 2500;
\echo '== Q06 check-in: último check-in do aluno (aluno_id, order by id desc limit 1)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT latitude, longitude, registrado_em FROM presencas WHERE aluno_id = 2500 ORDER BY id DESC LIMIT 1;
\echo '== Q07 professor: contagem de presenças da palestra (5.000)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT count(*) FROM presencas WHERE aula_id = (SELECT max(id) FROM aulas);
\echo '== Q08 professor: lista completa da palestra (apos_id=0, limite 2000)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT p.id, a.nome, a.matricula, a.email, c.nome, a.periodo, p.distancia_m, p.registrado_em, p.alertas FROM presencas p JOIN alunos a ON a.id = p.aluno_id LEFT JOIN cursos c ON c.id = a.curso_id WHERE p.aula_id = (SELECT max(id) FROM aulas) AND p.id > 0 ORDER BY p.id LIMIT 2000;
\echo '== Q09 professor: atualização incremental (só as novas, apos_id perto do fim)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT p.id, a.nome, a.matricula, a.email, c.nome, a.periodo, p.distancia_m, p.registrado_em, p.alertas FROM presencas p JOIN alunos a ON a.id = p.aluno_id LEFT JOIN cursos c ON c.id = a.curso_id WHERE p.aula_id = (SELECT max(id) FROM aulas) AND p.id > (SELECT max(id) - 20 FROM presencas) ORDER BY p.id LIMIT 2000;
\echo '== Q10 professor: ausentes (curso 1, período 1; NOT IN presenças da aula 1)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT nome, matricula, email, periodo FROM alunos WHERE curso_id = 1 AND (status IS NULL OR status != 'inativo') AND id NOT IN (SELECT aluno_id FROM presencas WHERE aula_id = 12) AND periodo = 1 ORDER BY nome LIMIT 500;
\echo '== Q11 aluno: meus comprovantes (página 1)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT p.id, p.registrado_em, a.titulo, a.tipo, a.local_tipo, a.disciplina, a.periodo, pr.nome FROM presencas p JOIN aulas a ON a.id = p.aula_id JOIN professores pr ON pr.id = a.professor_id WHERE p.aluno_id = 2500 ORDER BY p.id DESC LIMIT 20 OFFSET 0;
\echo '== Q12 coordenação: resumo (contagens)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT count(*) FROM presencas;
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT c.nome, count(a.id) FROM cursos c LEFT JOIN alunos a ON a.curso_id = c.id GROUP BY c.id, c.nome ORDER BY c.nome;
\echo '== Q13 ANTES: coordenação, lista de aulas (join + group by em TODAS as aulas, depois limit 60)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT a.*, p.nome, count(pr.id), c.nome FROM aulas a JOIN professores p ON p.id = a.professor_id LEFT JOIN presencas pr ON pr.aula_id = a.id LEFT JOIN cursos c ON c.id = a.curso_id GROUP BY a.id, p.nome, c.nome ORDER BY a.ativa DESC, a.id DESC LIMIT 60 OFFSET 0;
\echo '== Q13 DEPOIS (1/2): lista de aulas, só as 60 da página'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT a.*, p.nome, c.nome FROM aulas a JOIN professores p ON p.id = a.professor_id LEFT JOIN cursos c ON c.id = a.curso_id ORDER BY a.ativa DESC, a.id DESC LIMIT 60 OFFSET 0;
\echo '== Q13 DEPOIS (2/2): contagem de presenças só das aulas da página'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT aula_id, count(id) FROM presencas WHERE aula_id IN (SELECT id FROM aulas ORDER BY ativa DESC, id DESC LIMIT 60) GROUP BY aula_id;
\echo '== Q14 coordenação: alertas (alertas IS NOT NULL, order by id desc limit 100)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT p.id, a.nome, a.email, au.titulo, p.alertas, p.ip, p.distancia_m, p.registrado_em FROM presencas p JOIN alunos a ON a.id = p.aluno_id JOIN aulas au ON au.id = p.aula_id WHERE p.alertas IS NOT NULL ORDER BY p.id DESC LIMIT 100;
\echo '== Q15 coordenação: auditoria (últimas 150)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT l.*, p.nome FROM logs_seguranca l LEFT JOIN professores p ON p.id = l.usuario_id ORDER BY l.id DESC LIMIT 150;
\echo '== Q16 coordenação: busca de aluno por texto (like %q% em nome/e-mail/matrícula)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT count(*) FROM alunos WHERE lower(nome) LIKE '%0420%' OR email LIKE '%0420%' OR lower(matricula) LIKE '%0420%';
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT a.*, c.nome FROM alunos a LEFT JOIN cursos c ON c.id = a.curso_id WHERE lower(a.nome) LIKE '%0420%' OR a.email LIKE '%0420%' OR lower(a.matricula) LIKE '%0420%' ORDER BY a.nome LIMIT 50;
\echo '== Q17 exportação CSV da palestra (5.000 presentes, order by nome)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT a.matricula, a.nome, a.email, c.nome, a.periodo, p.registrado_em, p.distancia_m FROM presencas p JOIN alunos a ON a.id = p.aluno_id LEFT JOIN cursos c ON c.id = a.curso_id WHERE p.aula_id = (SELECT max(id) FROM aulas) ORDER BY a.nome;
\echo '== Q18 anotações do aluno (200 mais novas)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT * FROM notas_aluno WHERE aluno_id = 2500 ORDER BY atualizado_em DESC LIMIT 200;
\echo '== Q19 matrículas (lista 50 + contagens)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT m.*, c.nome FROM matriculas_autorizadas m LEFT JOIN cursos c ON c.id = m.curso_id ORDER BY m.matricula LIMIT 50;
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT count(*) FROM matriculas_autorizadas WHERE utilizada;
