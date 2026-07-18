# Whispered Live: compatibility matrix

Этот файл — журнал ручной приёмки L14. Один кейс означает 20-минутный live
прогон с включёнными mic и system audio. `not run` — честное состояние до
реального прогона; кодовая матрица хранится в
`core/live/compatibility.py` и не подменяет проверку Zoom/Meet/Teams.

| Приложение | Capture | Permissions | Device switch | Echo | Stop | Итог |
|---|---|---|---|---|---|---|
| Zoom | not run | not run | not run | not run | not run | not run |
| Google Meet | not run | not run | not run | not run | not run | not run |
| Microsoft Teams | not run | not run | not run | not run | not run | not run |

## Чек-лист одного прогона

1. Запустить приложение в standalone `.app`, выбрать mic и конкретное окно
   Zoom/Meet/Teams; убедиться, что собственное playback Whispered не взято.
2. Проверить разрешения Microphone и Screen Recording, затем начать 20 минут
   воспроизведения/разговора с речью в обоих источниках.
3. Во время прогона один раз переключить input device и убедиться, что
   discontinuity видна, а доступный source продолжает работать.
4. Повторить контрольный echo/monitoring фрагмент: дубль не должен появиться
   второй финальной репликой, а разные одинаковые по тексту фразы в разное
   время должны сохраниться.
5. Нажать Stop, дождаться финала и сохранить этот отчёт вместе с датой,
   версией приложения, моделью Whisper и counters drops/duplicates.

Ручная приёмка L14 остаётся незавершённой до появления Swift helper и
standalone capture stack; текущий status `not run` намеренный.
