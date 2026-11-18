# Guará Check e a LGPD

Este arquivo explica **o que o sistema já faz** pela Lei Geral de Proteção de Dados (Lei 13.709/2018) e **o que a instituição ainda precisa
fazer**. O que está aqui é apoio técnico, **não é parecer jurídico**: o texto de `/privacidade` e as bases legais são um **modelo** e precisam
ser revisados pelo encarregado (DPO) e pelo jurídico da UniCerrado antes do uso oficial.

## O que o sistema faz

| Tema | Como funciona |
|---|---|
| **Aviso de privacidade público** | `/privacidade` (sem login): dados coletados, finalidades, bases legais, quem vê, prazos, direitos e contato do encarregado. Link no rodapé do app. |
| **Aceite da localização** | O aluno aceita o aviso antes de registrar presença (cartão na tela do aluno). Guarda data e versão do texto. O navegador só pede a localização **depois** do aceite. O servidor recusa entrada e saída sem o aceite (`403`). Trocar `LGPD_VERSAO` no `app.py` exige novo aceite de todos. |
| **Revogar o aceite** | Em *Meus dados e privacidade*. Vale daqui para a frente (bloqueia novos registros pelo app; a presença já registrada continua). Quem não aceita pode pedir ao professor a **presença manual**, que não usa a localização do aluno. |
| **Baixar meus dados** | Arquivo JSON com cadastro, presenças (com posição, IP e aparelho, se ainda existirem), tentativas recusadas, anotações privadas e pedidos. Só do próprio aluno; fica na auditoria. |
| **Pedidos do titular** | O aluno pede **excluir a conta**, **corrigir dados** ou **receber informações**. A coordenação atende ou recusa (recusa exige explicação) na aba **LGPD**, com alerta depois de 15 dias. Atender a exclusão apaga conta, presenças, tentativas e anotações e libera a matrícula; o pedido fica registrado, sem texto e sem nome. |
| **Retenção** | Depois de `LGPD_RETENCAO_DIAS` (padrão 180) cada presença perde a posição exata (passa a valer o ponto da sala), o identificador e a impressão digital do aparelho e o IP; as tentativas recusadas perdem posição, IP e detalhe; os registros de segurança perdem o IP depois de `LGPD_RETENCAO_LOGS_DIAS` (padrão 180). Roda sozinha 1 vez por dia (e 1 minuto depois de o servidor subir), em lotes, e a coordenação pode **simular** e **rodar agora** na aba LGPD. A presença em si (aluno, evento, data, hora, permanência, distância até a sala) continua valendo para comprovante e certificado. |
| **Menos dados para o professor** | O professor vê nome, matrícula, e-mail, curso, horário, distância e alertas, **nunca** as coordenadas, o IP nem o aparelho. Ninguém, nem a coordenação, lê as anotações privadas. |

## Variáveis de ambiente

| Variável | Padrão | Para quê |
|---|---|---|
| `LGPD_CONTROLADOR` | UniCerrado - Centro Universitário de Goiatuba | Nome do controlador na página `/privacidade`. |
| `LGPD_ENCARREGADO_NOME` / `LGPD_ENCARREGADO_EMAIL` | vazio | Contato do encarregado. **Preencha.** Vazio: a página avisa que o contato ainda não foi informado e o servidor registra um aviso no log ao subir. |
| `LGPD_EXIGIR_ACEITE` | `1` | `0` desliga a exigência do aceite (não recomendado). |
| `LGPD_RETENCAO_DIAS` | `180` | Dias até apagar posição exata, aparelho e IP das presenças e tentativas. `0` = nunca. |
| `LGPD_RETENCAO_LOGS_DIAS` | `180` | Dias até apagar o IP dos registros de segurança. `0` = nunca. |

## O que a instituição ainda precisa fazer

1. **Nomear o encarregado (DPO)** e preencher `LGPD_ENCARREGADO_NOME` e `LGPD_ENCARREGADO_EMAIL` no ambiente (Render: *Environment*).
2. **Revisar o texto de `/privacidade`** (função `pagina_privacidade` no `app.py`) com o jurídico: bases legais (o modelo cita consentimento para a
   localização e atividades acadêmicas/legítimo interesse para frequência e antifraude), prazos e o nome do controlador. Ao mudar o texto de forma
   relevante, troque `LGPD_VERSAO` para todos aceitarem de novo.
3. **Confirmar os prazos de retenção** com o jurídico. O padrão de 180 dias para IP em registros de segurança foi escolhido por causa do prazo de
   6 meses de guarda de registros de acesso do Marco Civil da Internet (art. 15), que **talvez** se aplique; a instituição deve confirmar se se aplica.
4. **Documentar o tratamento**: registro das operações (art. 37), avaliação de impacto/RIPD se o encarregado entender necessária (localização de
   milhares de alunos) e plano de resposta a incidentes (comunicação à ANPD e aos alunos, art. 48).
5. **Contratos com quem opera os dados**: hospedagem (Render ou a infraestrutura institucional), envio de e-mail (Brevo) e, se ligado, o proxycheck.io
   (consulta de VPN envia o IP do aluno). A página `/privacidade` só cita o proxycheck.io quando `PROXYCHECK_KEY` está definida.
6. **Definir o prazo de resposta interno** aos pedidos (a lei fala em até 15 dias para a declaração completa; o painel alerta depois disso).

## Limites que vale conhecer

- **Backups**: apagar no sistema não apaga das cópias de segurança (`pg_dump`, snapshots do provedor). Defina por quanto tempo os backups ficam.
- **Logs de infraestrutura**: o provedor de hospedagem e o proxy reverso têm os próprios logs de acesso (com IP), fora do controle do app.
- **E-mails já enviados** (código de confirmação, avisos de chamada) ficam no provedor de e-mail conforme a política dele.
- **Excluir a conta apaga os comprovantes**: o aluno é avisado para baixar os PDF antes. A coordenação pode recusar o pedido com explicação
  (por exemplo, certificado em andamento).
- **Presenças lançadas pelo professor** (manual) guardam o ponto da sala e a justificativa escrita pelo professor; a justificativa não é
  apagada pela retenção.
- **O que o app não faz sozinho**: não envia e-mail ao aluno quando o pedido é respondido (ele vê a resposta em *Meus dados e privacidade*).
- **Teste em PostgreSQL**: os testes automáticos rodam com um banco de mentira; as consultas novas (`IS NOT NULL`, `OR`, `IN`, lotes) precisam
  ser conferidas uma vez no PostgreSQL de verdade (aba LGPD: a contagem de “Se a limpeza rodasse agora” e depois *Rodar a limpeza agora* em um banco de teste).

## Como operar

- **Coordenação > LGPD**: quantos alunos aceitaram a versão atual, pedidos abertos (com dias em aberto), simulação e execução da limpeza.
- **Auditoria** (`logs_seguranca`): `aceite_lgpd`, `revogou_lgpd`, `dados_titular`, `pedido_lgpd`, `resp_pedido_lgpd`, `retencao_lgpd` (limpeza
  automática, só quando apagou algo) e `retencao_manual`.
- **Testes**: `pytest tests/test_func19_lgpd.py` e `python3 tests/navegador/lgpd_tela.py`.
