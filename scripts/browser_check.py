import os

from playwright.sync_api import expect, sync_playwright


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH"))
        page = browser.new_page(locale="ru-RU", timezone_id="Europe/Moscow")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        expect.set_options(timeout=30000)
        page.goto(os.getenv("RISKFLOW_URL", "http://127.0.0.1:8000"))
        page.locator("#syntheticCase").select_option(index=1)
        page.locator("#launch select").select_option("credit-risk")
        expect(page.locator("#scenarioExpected")).to_contain_text("Ожидаемое значение")
        expected = page.locator("#scenarioExpected").inner_text().split(" · ")[1].split()[0]
        page.get_by_role("button", name="Запустить расчёт").click()
        detail = page.get_by_role("dialog")
        expect(detail).to_contain_text("SUCCEEDED")
        expect(detail.locator("pre")).to_contain_text(expected)
        detail.get_by_role("button", name="Воспроизвести").click()
        expect(detail).to_contain_text("Исходная задача")
        expect(detail).to_contain_text("SUCCEEDED")
        detail.get_by_role("button", name="Закрыть").click()
        page.get_by_role("button", name="Проверить систему").click()
        expect(page.locator("#batchSteps").get_by_text("Совпало", exact=True)).to_have_count(5)
        page.get_by_role("button", name="Демо", exact=True).click()
        page.get_by_role("button", name="Запустить демонстрацию").click()
        expect(page.locator("#demoStatus")).to_contain_text("Задачи завершены")
        expect(page.locator("#demoBars [marker-end]")).to_have_count(1)
        expect(page.locator("#demoBars svg text")).to_have_count(3)
        expect(page.locator("#demoJobs").get_by_text("SUCCEEDED")).to_have_count(3)
        expect(page.locator("#demoBars")).to_contain_text("Регламент · credit-risk")
        bars = page.locator("#demoBars svg rect").evaluate_all(
            "(rects) => rects.map(r => [Number(r.getAttribute('x')),Number(r.getAttribute('width'))])"
        )
        first, scheduled, liquidity = bars[1], bars[3], bars[5]
        assert first[0] <= liquidity[0] < first[0] + first[1] <= scheduled[0] + 1e-6, bars
        page.locator("#demoBars").get_by_role("button", name="Владелец ресурса").click()
        expect(detail).to_contain_text("credit-risk · SUCCEEDED")
        assert not errors, errors
        print("Проверены ручной запуск, воспроизведение, пять контрольных результатов и таймлайн демо.")
        browser.close()


if __name__ == "__main__":
    main()
