# Autonomous AI Agent (GUI)

Версия: `v0.62.7b`

Desktop AI-агент с графовым runtime (`LangGraph`) и GUI на `PySide6`.
Приложение ориентировано на повседневную разработку: работа с файлами проекта, запуск инструментов, безопасные approvals для рискованных действий и удобная история чатов по проектам.

## Ключевые возможности

- Чат-интерфейс с persistent-сессиями по проектам.
- Sidebar с историей чатов, быстрым переключением и удалением сессий.
- Transcript с компактным отображением шагов агента и инструментов.
- Composer с автоподстройкой высоты и отправкой `Enter` (`Shift+Enter` — новая строка).
- Быстрое добавление файлов через `@`:
  - popup-список рядом с курсором,
  - фильтрация по имени/пути,
  - навигация `↑/↓`, выбор `Enter`, закрытие `Esc`,
  - приоритет файлов из корня проекта, затем из подпапок.
- История запросов в composer:
  - навигация `↑/↓` как в терминале,
  - работает по текущей сессии,
  - восстановление после перезапуска из transcript.
- Approval-поток для mutating/destructive инструментов (`Approve`, `Deny`, `Always for this session`).
- Поддержка MCP-инструментов (включая Context7 при соответствующей конфигурации).
- Runtime-статусы (`Thinking`, `Reviewing`, `Ready`, ошибки) в UI.

## Архитектура

- [`agent.py`](agent.py): сборка агентного графа, LLM, реестр инструментов, checkpoint backend.
- [`main.py`](main.py): главное окно GUI, меню, sidebar, transcript, composer, обработка UI-событий.
- [`core/gui_runtime.py`](core/gui_runtime.py): runtime-контроллер, запуск графа, стриминг событий, переключение/восстановление сессий.
- [`core/gui_widgets.py`](core/gui_widgets.py): UI-виджеты (composer, transcript, popup, tool cards, sidebar).
- [`core/session_store.py`](core/session_store.py): хранение и индекс сессий.
- [`core/ui_theme.py`](core/ui_theme.py): единая тема и стили.
- [`core/config.py`](core/config.py): загрузка env-конфигурации.
- [`tools/tool_registry.py`](tools/tool_registry.py): локальные + MCP инструменты, метаданные, политика безопасности.

## Хранение данных

- Checkpoint state: `CHECKPOINT_SQLITE_PATH` (или другой backend).
- Активная сессия: `SESSION_STATE_PATH` (по умолчанию `.agent_state/session.json`).
- Индекс чатов: `.agent_state/session_index.json`.
- Логи запусков: `RUN_LOG_DIR` (JSONL).

При старте runtime текущая активная сессия и transcript автоматически восстанавливаются.

## Быстрый старт

1. Создать виртуальное окружение:

```bash
python -m venv venv
```

2. Установить зависимости:

```bash
pip install -r requirements.txt
```

3. Подготовить `.env`:

```powershell
Copy-Item env_example.txt .env
```

4. Запустить приложение:

```bash
python main.py
```

## Основные переменные окружения

### LLM-провайдер

- `PROVIDER=gemini|openai`
- `GEMINI_API_KEY`, `GEMINI_MODEL`
- `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BASE_URL`

### Runtime и persistence

- `PROMPT_PATH`
- `MCP_CONFIG_PATH`
- `CHECKPOINT_BACKEND=sqlite|memory|postgres`
- `CHECKPOINT_SQLITE_PATH`
- `CHECKPOINT_POSTGRES_URL`
- `SESSION_STATE_PATH`
- `RUN_LOG_DIR`

### Инструменты и безопасность

- `MODEL_SUPPORTS_TOOLS`
- `ENABLE_FILESYSTEM_TOOLS`
- `ENABLE_SEARCH_TOOLS` (+ `TAVILY_API_KEY`)
- `ENABLE_SYSTEM_TOOLS`
- `ENABLE_PROCESS_TOOLS`
- `ENABLE_SHELL_TOOL`
- `ENABLE_APPROVALS`
- `ALLOW_EXTERNAL_PROCESS_CONTROL`

### Лимиты

- `MAX_TOOL_OUTPUT`
- `MAX_SEARCH_CHARS`
- `MAX_FILE_SIZE`
- `MAX_READ_LINES`
- `MAX_BACKGROUND_PROCESSES`

### Контекст и retry

- `SESSION_SIZE`
- `SUMMARY_KEEP_LAST`
- `MAX_RETRIES`
- `RETRY_DELAY`

### Диагностика

- `DEBUG`
- `STRICT_MODE`
- `LOG_LEVEL`
- `LOG_FILE`

## Управление сессиями

- `New Session` создает новый чат в текущем проекте.
- `Open Folder` переключает рабочую папку и контекст сессий.
- Sidebar показывает историю чатов с группировкой по проектам.

## Горячие клавиши в Composer

- `Enter` — отправить запрос.
- `Shift+Enter` — новая строка.
- `@` — открыть popup файлов.
- `↑/↓` — навигация по popup или истории запросов (в зависимости от контекста).
- `Esc` — закрыть popup.

## Тесты

Запуск полного набора:

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

Ключевые наборы:

- [`tests/test_cli_ux.py`](tests/test_cli_ux.py)
- [`tests/test_runtime_refactor.py`](tests/test_runtime_refactor.py)
- [`tests/test_stream_and_filesystem.py`](tests/test_stream_and_filesystem.py)
- [`tests/test_tooling_refactor.py`](tests/test_tooling_refactor.py)

## Безопасность

- Не храните реальные API-ключи в репозитории.
- Включайте `ENABLE_SHELL_TOOL=true` только при необходимости и с approvals.
- Для production persistence рекомендуется `postgres` backend.
- Проверяйте политику инструментов перед запуском mutating/destructive действий.
