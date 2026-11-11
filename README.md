# Rusaoc Cup

Статический сайт со статистикой Age of Empires II: поиск игроков, сортировка, карточки выступлений и флаги стран.

- `site/index.html` — выбор версии.
- `site/rusaoc-cup-statistics.html` — результаты и призовые места из Google Таблицы организаторов, страны из Liquipedia.
- `site/rusaoc-cup-liquipedia.html` — участники, результаты сеток и победители по страницам Liquipedia.

Статистика двух источников не смешивается: их покрытие различается. Страны берутся из профиля игрока, а при его отсутствии — из последних явных указаний на страницах турниров. Неизвестные страны не угадываются.

## GitHub Pages

Репозиторий должен быть публичным для использования GitHub Pages на GitHub Free.

1. В **Settings → Pages → Build and deployment → Source** выбрать **GitHub Actions**.
2. В **Actions → Update and publish statistics → Run workflow** запустить публикацию. Для публикации уже сохранённых данных снять `Download fresh statistics before publishing`.
3. Адрес сайта: <https://ryzhikoff.github.io/rusaoc-cup/>.

Workflow запускается при изменениях `main`, вручную и ежедневно в **08:17 по Томску** (`17 1 * * *`, UTC). Запуск GitHub может задерживаться. Обновление по расписанию загружает оба источника, проверяет данные, сохраняет HTML коммитом и публикует каталог `site/`.

При загрузке или проверке с ошибкой новый сайт не публикуется. Посетителям остаётся доступен предыдущий срез. Публичные страницы не обращаются к API, не сохраняют статистику в браузере и не содержат кнопки обновления.

Автоматические коммиты обновляют историю репозитория; расписания публичных репозиториев GitHub отключаются после 60 дней без активности. При длительных сбоях обновления следует проверить вкладку Actions. `GITHUB_TOKEN` создаётся GitHub для каждого запуска; отдельные секреты для публикации и публичных источников не нужны. Политики организации или защита ветки могут запретить автоматический коммит — workflow должен иметь право записи в `main`.

## Локальная работа

Нужен Python 3.10 или новее, сторонних зависимостей нет. Workflow использует Python 3.13.

```sh
python3 -m unittest discover -s tests -v
python3 scripts/update.py
python3 scripts/check_site.py
python3 -m http.server 8000 --directory site
```

Открыть <http://localhost:8000>. HTML также открывается прямо из файла.

`scripts/update.py` получает Google Таблицу, архив Liquipedia и страны игроков. Оба HTML заменяются только после получения и проверки всех данных. Новые события и игроки включаются автоматически, если структура источников осталась прежней. Резкое сокращение архива или числа игроков, несовпадающие итоги таблицы и неполные API-ответы останавливают обновление.

## Источники

- [Google Таблица Rusaoc Cup](https://docs.google.com/spreadsheets/d/1zDXLyhJwHMQhtCvrNnvTV8H3tVx2WRyXRlqzmrIZRwo/edit)
- [Liquipedia Rusaoc Cup](https://liquipedia.net/ageofempires/Rusaoc_Cup)
- [GitHub Pages workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
- [GitHub Actions schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

Это неофициальная сводка сообщества. Ссылки на исходные страницы и ограничения полноты данных доступны на сайте.
