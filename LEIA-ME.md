# North Robô

Confere a pasta do Drive a cada 5 minutos e manda para o seu Telegram:
- a **grade** do dia, assim que a versão de hoje aparecer (e avisa se passar das 12h sem atualização);
- cada **arte de torneio** 15 min antes do início (exceto satélites), já com a logo da North no rodapé.

O horário e a data vêm do topo de cada arte ("26/10 • SEGUNDA • 15:05"). Arte de outro dia nunca é enviada.

## Colocar no ar (GitHub, grátis)
1. Crie conta em github.com e clique em **New repository**. Nome: `north-robo`. Marque **Public** (assim o tempo de execução é ilimitado; nenhuma senha fica no código) e **Create**.
2. Descompacte este zip. No repositório, clique em **uploading an existing file** e arraste TUDO que está dentro da pasta `robo` (robo.py, requirements.txt, logo_north_branca.png, state.json, LEIA-ME.md, robo.yml). Clique em **Commit changes**.
3. Ainda no repositório: **Add file → Create new file**. No nome digite exatamente `.github/workflows/robo.yml` (as barras criam as pastas). Cole dentro o conteúdo do arquivo `robo.yml` deste pacote e clique em **Commit changes**.
4. **Settings → Secrets and variables → Actions → New repository secret**. Crie estes 6, um por vez (nome exato, valor sem aspas):
   - `TELEGRAM_TOKEN` — token do seu bot
   - `TELEGRAM_CHAT_ID` — `5248058917`
   - `GOOGLE_CLIENT_ID` — está no JSON do Google (`client_id`)
   - `GOOGLE_CLIENT_SECRET` — está no JSON do Google (`client_secret`)
   - `GOOGLE_REFRESH_TOKEN` — o texto gerado pelo autorizar_drive.html
   - `DRIVE_FOLDER_ID` — `1CrFJ_EoTzK_8L38kgUoAl1Og21inr0QU`
5. **Settings → Actions → General → Workflow permissions**: marque **Read and write permissions** e salve.
6. Aba **Actions → North Robo → Run workflow** para testar. Ele deve terminar verde. Se houver grade de hoje no Drive, ela chega no seu Telegram.

## Ajustes (variáveis no workflow)
`ANTECEDENCIA_MIN` (15), `JANELA_APOS_MIN` (30: ainda envia se o robô atrasar), `PRAZO_GRADE_H` (12: hora do aviso de grade atrasada).

## Autorização do Google
Em modo "Testando" a autorização dura 7 dias. Quando expirar, o robô avisa no Telegram: gere nova chave no `autorizar_drive.html` e troque o secret `GOOGLE_REFRESH_TOKEN`.
