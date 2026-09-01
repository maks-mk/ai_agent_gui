# MCP (Model Context Protocol)

`mcp.json` хранит конфигурацию опциональных MCP-серверов и сохранённое состояние встроенных инструментов. Путь к файлу задаётся через `MCP_CONFIG_PATH` (по умолчанию `mcp.json`). Переменные окружения в строковых значениях конфигурации раскрываются при загрузке.

В текущем шаблоне доступны `context7` (remote HTTP, read-only, включён) и `sequential-thinking` (stdio, выключен). Поле `enabled` управляет загрузкой сервера. После ручного изменения конфигурации перезапустите приложение; UI сохраняет состояние в этот же файл.

Служебный объект `_builtin_tools` не является MCP-сервером. UI записывает в него пользовательские overrides для встроенных инструментов, например `"download_file": false`. Если override отсутствует, состояние определяется соответствующим feature flag из `.env`.

## Policy

Поддерживается только флаг `policy.read_only`. Его можно задать для всего сервера и переопределить для отдельного инструмента через `policy.tools.<tool_name>.read_only`.

- `true`: инструмент считается read-only и не требует approval.
- `false`: инструмент считается изменяющим состояние и требует approval.
- Флаг не указан: агент использует MCP metadata hints; если сервер не предоставил достаточные hints, применяется консервативный режим с approval.
- `ENABLE_APPROVALS=false`: глобально отключает запросы approval независимо от MCP policy.

Пример policy для отдельного инструмента:

```json
{
  "example-server": {
    "transport": "stdio",
    "command": "example-mcp-server",
    "enabled": true,
    "policy": {
      "read_only": true,
      "tools": {
        "update_record": {
          "read_only": false
        }
      }
    }
  }
}
```

## Сжатие вывода

Для MCP не нужны отдельные параметры сжатия в `mcp.json`. Инструмент определяется по runtime metadata `source == "mcp"`, а общий pipeline управляется параметрами `ENABLE_HEADROOM_COMPRESSION` и `MAX_TOOL_OUTPUT` из `.env`.

1. Результаты типа `dict` и `list` сериализуются в JSON через `json.dumps(..., ensure_ascii=False)`. Строковые результаты сохраняются как есть.
2. Ответ размером не больше `MAX_TOOL_OUTPUT` передаётся модели без сжатия.
3. Для большего ответа при `ENABLE_HEADROOM_COMPRESSION=true` вызывается `HeadroomMCPCompressor`.
4. При успешном сжатии используется только непустой результат, который меньше исходного и не содержит неразрешимый CCR marker.
5. После семантического этапа результат в любом случае ограничивается до `MAX_TOOL_OUTPUT`.

Текущий агент не регистрирует инструмент `headroom_retrieve`, поэтому CCR markers вида `<<ccr:...>>` были бы бесполезны для модели. Такой результат Headroom отклоняется. При passthrough, ошибке, недоступности Headroom или CCR marker агент применяет детерминированный fallback: сохраняет начало и конец ответа, а удалённую середину отмечает как `[OMITTED N chars]`.

Plain text в Headroom 0.37.0 обычно проходит без семантического сжатия. Для ответа больше лимита это не отменяет финальный fallback до `MAX_TOOL_OUTPUT`.

## Удалённый сервер

```json
{
  "context7": {
    "type": "remote",
    "url": "https://mcp.context7.com/mcp",
    "transport": "http",
    "enabled": true,
    "policy": {
      "read_only": true
    }
  }
}
```
