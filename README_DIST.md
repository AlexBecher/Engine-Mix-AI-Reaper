# Alex Studio MIX - AI

Sistema de mixagem automatizada com IA para controle de faders no REAPER em tempo real.

---

## Requisitos no PC destino

- Windows 10 ou 11 (64 bits)
- REAPER com plugin **ReaStream** instalado e ativo
- REAPER com **Web Interface** habilitada (Preferences > Control/OSC/web)
- **Microsoft Visual C++ Redistributable 2015-2022 x64**
  - Download: https://aka.ms/vs/17/release/vc_redist.x64.exe

---

## Como rodar

1. Execute `Run_AlexStudioMix.bat` (ou diretamente `AlexStudioMix.exe`)
2. Na primeira execucao, configure os IPs e portas no painel **Run settings**
3. Clique **SAVE** para salvar as configuracoes
4. Clique **RUN PROFILE** para iniciar o processamento

---

## Configuracao de IPs (painel Run settings)

Todos os IPs sao configurados diretamente pela interface ? nao e necessario editar arquivos manualmente.

| Campo | O que e | Exemplo |
|---|---|---|
| **Web API IP** | IP do PC onde o REAPER roda | `127.0.0.1` (mesmo PC) ou IP da rede |
| **Web API Port** | Porta da Web Interface do REAPER | `8080` |
| **Web API Base** | Base path da API do REAPER | `/_` |
| **ReaStream IP/Filtro** | Origem do audio UDP | `0.0.0.0` = qualquer origem |
| **ReaStream Port** | Porta UDP do ReaStream | `58710` |

### Dica de rede
- **REAPER no mesmo PC**: `Web API IP = 127.0.0.1`, `ReaStream IP = 0.0.0.0`
- **REAPER em outro PC na rede**: `Web API IP = <IP do PC do REAPER>`, `ReaStream IP = 0.0.0.0` ou `<IP do PC do REAPER>`

---

## Configuracao do REAPER

### ReaStream
1. Em uma track master de saida, insira o efeito **ReaStream**
2. Modo: **Send audio** (UDP)
3. Identifier: igual ao campo **Identifier** na GUI (padrao: `master`)
4. Port: igual ao campo **ReaStream Port** (padrao: `58710`)
5. Local: pode deixar `0.0.0.0` para broadcast na rede local

### Web Interface
1. REAPER > Preferences > Control/OSC/web > Add
2. Tipo: **Web browser interface**
3. Port: igual ao campo **Web API Port** (padrao: `8080`)
4. Marque **Allow local connections** e, se necessario, **Allow remote connections**

---

## Perfis de mix

- Os perfis ficam em `learning\profiles.json`
- Selecione o perfil desejado no campo **Profile** antes de iniciar
- Perfis aprendidos via botao **LEARN** sao salvos automaticamente

---

## Dry Run (teste sem audio ao vivo)

Permite testar o sistema com arquivo de audio ou dispositivo de entrada sem usar o ReaStream.

1. Ative a opcao **Dry Run** na interface
2. Selecione a fonte: arquivo de audio, dispositivo ou ReaStream simulado
3. Clique **DRY RUN** ? o log e salvo na pasta `logs\`

---

## Estrutura da pasta de instalacao

```
AlexStudioMix\
  AlexStudioMix.exe       - Aplicativo principal (interface grafica)
  run_profile_worker.exe  - Engine de processamento (iniciado automaticamente)
  config.json             - Configuracoes (salvas pela interface)
  learning\
    profiles.json         - Perfis de mix
  logs\                   - Logs de debug e dry-run
  img\                    - Icones da interface
  Run_AlexStudioMix.bat   - Atalho de inicializacao
```

---

## Solucao de problemas

| Sintoma | Causa provavel | Solucao |
|---|---|---|
| Janela nao abre / erro de DLL | Falta do VC++ Redistributable | Instalar vc_redist.x64.exe |
| `Web API: ERRO` no painel | IP/porta errados ou REAPER nao esta rodando | Verificar Web API IP e Port |
| `ReaStream: aguardando...` | ReaStream nao enviando / porta errada | Verificar configuracao do plugin no REAPER |
| Faders nao se movem | Track IDs incorretos | Verificar IDs das tracks no painel Tracks |
| `Worker nao encontrado` | run_profile_worker.exe ausente | Recopiar a pasta dist completa |
