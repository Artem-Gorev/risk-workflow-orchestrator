import os
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH"))
        page = browser.new_page(locale="ru-RU", timezone_id="Europe/Moscow")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        expect.set_options(timeout=30000)
        response = page.goto(os.getenv("RISKFLOW_URL", "http://127.0.0.1:8000"))
        assert response is not None and response.status == 200
        expect(page.get_by_text("API подключён", exact=True)).to_be_visible()
        scenario_result = page.locator(".scenario-result")
        expect(scenario_result).to_be_visible()
        expected = scenario_result.inner_text()
        page.get_by_role("button", name="Запустить расчёт").click()
        status = page.locator(".job-heading .status")
        output = page.locator(".job-result .output-value")
        expect(status).to_have_text("Выполнена")
        expect(output).to_have_text(expected)
        original_url = page.url
        page.get_by_role("button", name="Воспроизвести по снимку").click()
        expect(page).not_to_have_url(original_url)
        original_link = page.locator(f'a[href="#{urlsplit(original_url).fragment}"]')
        expect(original_link).to_be_visible()
        expect(status).to_have_text("Выполнена")
        expect(output).to_have_text(expected)

        navigation = page.get_by_role("navigation", name="Основная навигация")
        navigation.get_by_role("link", name="Сводка рисков").click()
        page.get_by_role("button", name="Запустить серию").click()
        steps = page.get_by_role("table")
        expect(steps.get_by_text("Совпадает", exact=True)).to_have_count(5)

        navigation.get_by_role("link", name="Демо конкуренции").click()
        page.get_by_role("button", name="Запустить демонстрацию").click()
        demo_jobs = page.locator(".diagnostic-demo-jobs")
        expect(demo_jobs.get_by_text("Выполнена", exact=True)).to_have_count(3)
        timeline = page.locator(".diagnostic-lanes")
        expect(timeline.locator(".diagnostic-segment.diagnostic-waiting")).to_have_count(1)
        running = timeline.locator(".diagnostic-segment.diagnostic-running")
        expect(running).to_have_count(3)
        bars = running.evaluate_all(
            "(bars) => bars.map(bar => [parseFloat(bar.style.left), parseFloat(bar.style.width)])"
        )
        first, scheduled, liquidity = bars
        assert first[0] <= liquidity[0] < first[0] + first[1] <= scheduled[0] + 1e-6, bars
        page.locator(".diagnostic-section").get_by_role("link", name="A", exact=True).click()
        expect(page.get_by_role("heading", name="Кредитный риск", exact=True)).to_be_visible()
        expect(status).to_have_text("Выполнена")
        assert not errors, errors
    print("Проверены ручной запуск, воспроизведение, пять контрольных результатов и таймлайн демо.")


if __name__ == "__main__":
    main()
