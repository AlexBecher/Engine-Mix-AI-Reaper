# GUI Configuration System - Implementation Summary

## What Was Implemented

A complete graphical configuration system for Mix Robo that allows you to:
- Configure track IDs, names, and dB limits
- Enable/disable individual tracks (mute functionality)
- Adjust analysis parameters (slew rates, error gain, deadband, etc.)
- Save all settings to an external config.json file
- Hot-reload configuration while the script is running

## ?? New Files Created

### 1. **config.json**
- JSON configuration file storing all settings
- Loaded at script startup and every analysis cycle
- Can be edited manually or through the GUI

### 2. **config_manager.py**
- Python module that loads/saves configuration
- Functions:
  - `load_config()` - Load from JSON
  - `save_config()` - Save to JSON
  - `get_track_db_limits()` - Return dict of track ID ? (min, max) dB
  - `get_enabled_tracks()` - Return set of enabled track IDs
  - `get_analysis_settings()` - Return settings dict
  - `get_master_track()` - Return master track ID

### 3. **config_gui.py**
- Tkinter-based graphical interface
- Three main sections:
  - **Master Track**: Single input for master track ID
  - **Tracks**: Rows for each track (enable, ID, name, min/max dB)
  - **Analysis Settings**: 7 tunable parameters
- Features:
  - Real-time editing
  - [SAVE] Button to persist changes
  - [RELOAD] Button to refresh from file
  - [RESET] Button to restore defaults

### 4. **run_gui.bat**
- Windows batch file launcher
- Double-click to open the GUI easily
- Or: `python config_gui.py`

### 5. **launcher.py** (Optional)
- Menu-driven Python launcher
- Allows choosing between GUI and script execution

### 6. **Documentation**
- **CONFIG_GUIDE.md** - Detailed configuration walkthrough
- **GUI_TUTORIAL.md** - GUI usage and features

## ?? Integration with Existing Scripts

### mix_profile.py Changes
- ? Now loads config.json automatically at startup
- ? Calls `_reload_config()` every analysis cycle
- ? Respects ENABLED_TRACKS - skips disabled tracks
- ? Uses TRACK_DB_LIMITS from config
- ? Uses analysis settings from config
- ? Logs in English (no encoding issues)

### Hot-Reload Capability
During processing, the script automatically picks up config changes:
```
Terminal 1: python run_profile.py --profile worship --reastream ...
Terminal 2: python config_gui.py
            (make changes, click Save)
Terminal 1: Next cycle (~5s) loads new config
```

## ?? Configuration Structure

### config.json Format
```json
{
  "master_track": 153,
  "tracks": {
    "154": {
      "name": "Drums",
      "enabled": true,
      "min_db": -3.0,
      "max_db": 3.0
    },
    ...
  },
  "analysis_settings": {
    "error_gain_up": 1.2,
    "error_gain_down": 2.2,
    "max_step_up_db": 0.10,
    "max_step_down_db": 0.35,
    "error_deadband": 0.18,
    "max_tracks_raise_per_cycle": 1,
    "lufs_warning_threshold": -14
  }
}
```

## ?? Key Features

### 1. Track Enable/Disable (Mute)
- Checkbox in GUI ? boolean in config
- When disabled, script logs: `[process] Track XXX disabled - skipping`
- Useful for temporarily excluding problem tracks

### 2. Per-Track Limits
- Each track has independent min/max dB range
- Example: Drums (-3 to +3 dB), Vocals (-20 to -6 dB)
- Prevents tracks from being over-adjusted

### 3. Analysis Parameters
- **Error Gain Up/Down** - Control boost vs cut aggressiveness
- **Max Step Up/Down** - Rate limit fader changes
- **Error Deadband** - Minimum error to act upon
- **LUFS Threshold** - Warning level for loudness

### 4. No Code Recompilation
- Pure Python, uses built-in tkinter
- Configuration is external (not in code)
- Easy to distribute and modify

## ?? Quick Start

### Open GUI
```powershell
# Option 1: Double-click batch file
run_gui.bat

# Option 2: Python command
.\.venv\Scripts\python config_gui.py

# Option 3: Launcher menu
.\.venv\Scripts\python launcher.py
```

### Save Changes
1. Make edits in GUI
2. Click **[SAVE] Configuration**
3. Status changes to "[OK] Config saved successfully!"
4. Running script picks up changes next cycle (~5s)

### Disabled Track Example
1. Open GUI
2. Uncheck the checkbox for Vocal (160)
3. Click [SAVE]
4. Script skips track 160 in next analysis
5. Check the console log: `[process] Track 160 disabled - skipping`

## Teste de LUFS por track no REAPER

O arquivo `test_web_API_reaper.py` pode ser usado para testar a leitura da Web API do REAPER e descobrir qual comando retorna meter/LUFS para uma track especifica.

### Pre-requisitos
- REAPER aberto
- Web Interface habilitada no REAPER
- IP, porta e base corretos da Web API

### Teste rapido de conexao
```powershell
.\.venv\Scripts\python test_web_API_reaper.py --host 127.0.0.1 --port 8080 --base /_
```

Se estiver tudo certo, o script mostra `REAPER online | NTRACK=...` e um resumo das primeiras tracks.

### Escutar e monitorar a track 2
1. No REAPER, deixe a `track 2` tocando ou em solo para ouvir somente ela.
2. Rode o scan de meter para descobrir qual endpoint da Web API traz LUFS/RMS dessa track:

```powershell
.\.venv\Scripts\python test_web_API_reaper.py --host 127.0.0.1 --port 8080 --base /_ --scan-meter 2 --only-scan
```

3. Veja no terminal qual linha aparece com `Parsed: LUFS=...` ou `Parsed: RMS=...dB`.
4. Enquanto a track estiver tocando, repita esse comando para acompanhar a resposta da API.

### Loop simples para ficar acompanhando a track 2
No PowerShell, voce pode deixar repetindo a leitura a cada 1 segundo:

```powershell
while ($true) {
  .\.venv\Scripts\python test_web_API_reaper.py --host 127.0.0.1 --port 8080 --base /_ --scan-meter 2 --only-scan
  Start-Sleep -Seconds 1
}
```

Use `Ctrl+C` para parar.

### Comandos uteis
- Ver apenas informacoes basicas da track 2:

```powershell
.\.venv\Scripts\python test_web_API_reaper.py --host 127.0.0.1 --port 8080 --base /_ --tracks 2
```

- Inspecionar a resposta crua da track 2:

```powershell
.\.venv\Scripts\python test_web_API_reaper.py --host 127.0.0.1 --port 8080 --base /_ --probe 2
```

- Testar somente um comando especifico de meter:

```powershell
.\.venv\Scripts\python test_web_API_reaper.py --host 127.0.0.1 --port 8080 --base /_ --scan-meter 2 --meter-command TRACK/{track}/LOUDNESS --only-meter-commands --only-scan
```

Se o REAPER estiver em outro computador da rede, troque `127.0.0.1` pelo IP desse computador.

## ?? For Distribution

Files to include when deploying to another machine:

1. **config.json** - User's configuration file
2. **config_manager.py** - Configuration loader
3. **config_gui.py** - GUI application
4. **run_gui.bat** - Convenience launcher
5. All existing files (mix_profile.py, run_profile.py, etc.)

User can then:
- Double-click `run_gui.bat` to configure
- Run: `python run_profile.py --profile worship --reastream ...` to process
- Changes take effect immediately

## ? Validation

All modules tested and working:
```
[SUCCESS] mix_profile loaded all configuration
  Master Track: 153
  Enabled Tracks: [154, 155, 156, 157, 158, 160]
  Total configured: 6
```

## ?? Known Limitations

- GUI is single-threaded (normal for tkinter)
- Config must be valid JSON (corrupted files require manual fix)
- Windows requires .bat file or PowerShell for GUI launch
- No remote GUI access (local only)

## ?? Possible Future Enhancements

- Web-based UI (Flask/FastAPI)
- Live monitoring dashboard
- Profile-specific presets
- Undo/redo history in GUI
- Config import/export
- Live fader visualization
- Automatic backup on save

---

**System Status:** ? **COMPLETE AND WORKING**

All configuration features ready for production use!
