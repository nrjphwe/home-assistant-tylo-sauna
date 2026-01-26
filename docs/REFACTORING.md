# Tylo Sauna — Refactoring Backlog

Идеи по улучшению кода, выявленные при анализе 2026-01-26.

> Часть сложностей связана с reverse engineering закрытого протокола Tylo.

## Критические

### 🔴 button.py:70-73 — Дублирование `async_press`

```python
async def async_press(self) -> None:
    self._controller.aroma_eucalyptus_off()
async def async_press(self) -> None:  # ← переопределяет предыдущий!
    await self._controller.async_ack_last_fault()
```

Вторая кнопка "aroma eucalyptus OFF" вызывает `ack_fault` вместо `aroma_off`.

---

## Важные

### 🟠 Нет `unregister_callback()` — утечка при reload

**Файл:** controller.py

```python
def register_callback(self, cb) -> None:
    self._callbacks.append(cb)
# Нет unregister!
```

При reload интеграции старые entity callbacks остаются → утечка памяти, дублирование вызовов.

**Решение:** Добавить `unregister_callback()`, вызывать в `async_will_remove_from_hass()`.

---

### 🟠 `telemetry_host` pinning без TTL

**Файл:** controller.py:820

```python
self.telemetry_host = src_ip  # pinned навсегда
```

Если после pinning IP сауны сменился (DHCP) — телеметрия от нового IP отклоняется навсегда до рестарта HA.

**Решение:** Добавить TTL или unpin при длительном отсутствии пакетов от pinned host.

---

### 🟠 Дублирование protobuf парсинга

`_decode_varint`, `_iter_fields`, `UUID_RE` дублируются в 3 файлах:
- controller.py
- config_flow.py
- runtime_discovery.py

**Решение:** Выделить в `protocol.py`.

---

### 🟠 Fire-and-forget команды без retry/confirmation

**Файл:** controller.py

```python
async def async_set_temperature(self, temp_c: float) -> None:
    self._send(payload, ...)  # UDP отправлен, успех не проверяется
```

UI показывает успех, но команда могла потеряться.

**Решение:** Для критичных команд (heat on/off) добавить:
- Ожидание confirmation в телеметрии
- Retry при таймауте
- Или хотя бы warning в логах если состояние не изменилось

---

## Средний приоритет

### 🟡 Discovery timeout 10s блокирует UI

**Файл:** config_flow.py:19

```python
UDP_DISCOVERY_TIMEOUT = 10.0
await asyncio.sleep(UDP_DISCOVERY_TIMEOUT)
```

**Решение:** Прогрессивный timeout или early exit при первом найденном устройстве.

---

### 🟡 Широкое использование `except Exception: pass`

30+ мест с паттерном:
```python
except Exception:  # noqa: BLE001
    pass
```

Затрудняет отладку, скрывает реальные ошибки.

**Решение:** Постепенно заменять на специфичные exception handlers с логированием.

---

## Архитектурные идеи (долгосрочно)

### God Object — SaunaController (~1160 строк)

Смешивает слишком много ответственностей. Потенциальное разделение:

```
TyloProtocol      — парсинг/сериализация protobuf
TyloTransport     — UDP сокеты, отправка/получение
SaunaState        — состояние устройства (dataclass)
SaunaController   — координация, бизнес-логика
```

### Неявная логика определения HEAT

```python
new_heat = self.stop_rem_min > 0
```

HEAT определяется как `stop_rem_min > 0`. Но если печка дожигает после stop_rem=0 — состояние неверное.

**Вопрос:** Есть ли explicit HEAT flag в протоколе? Требует анализа captures.

---

## Выполнено

- [ ] ...
