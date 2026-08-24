import logging
import re
import time

from prometheus_client import (
    CollectorRegistry,
    Gauge,
    push_to_gateway,
)

from selenium import webdriver
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


TARGET_URL = "http://89.232.176.168/"

USERNAME = "user2"
PASSWORD = "user2"

SELENIUM_TIMEOUT = 20
CHECK_INTERVAL = 300

PUSHGATEWAY_URL = "http://pushgateway:9091"
JOB_NAME = "weavesocks_synthetic"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


registry = CollectorRegistry()

transaction_duration = Gauge(
    "synthetic_transaction_duration_seconds",
    "Total transaction duration: sum of N01-N05",
    registry=registry,
)

transaction_success = Gauge(
    "synthetic_transaction_success",
    "Transaction result: 1 success, 0 failure",
    registry=registry,
)

step_duration = Gauge(
    "synthetic_step_duration_seconds",
    "Synthetic step duration in seconds",
    ["step"],
    registry=registry,
)

step_success = Gauge(
    "synthetic_step_success",
    "Synthetic step result: 1 success, 0 failure",
    ["step"],
    registry=registry,
)


STEP_NAMES = [
    "home",
    "auth",
    "catalogue",
    "add_to_cart",
    "cart",
    "remove_from_cart",
    "update_basket",
    "logout",
]


def create_driver():
    options = Options()

    options.binary_location = "/usr/bin/chromium"

    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")

    options.page_load_strategy = "eager"

    driver = webdriver.Chrome(
        options=options
    )

    driver.set_page_load_timeout(
        SELENIUM_TIMEOUT
    )

    return driver


def clear_previous_metrics():
    for step in STEP_NAMES:
        try:
            step_duration.remove(step)
        except KeyError:
            pass

        try:
            step_success.remove(step)
        except KeyError:
            pass


def execute_step(step_name, function):
    start = time.perf_counter()

    try:
        function()

        duration = (
            time.perf_counter()
            - start
        )

        step_duration.labels(
            step=step_name
        ).set(duration)

        step_success.labels(
            step=step_name
        ).set(1)

        logging.info(
            "[SUCCESS] %-20s %.3f sec",
            step_name,
            duration,
        )

        return True, duration

    except Exception:
        duration = (
            time.perf_counter()
            - start
        )

        step_duration.labels(
            step=step_name
        ).set(duration)

        step_success.labels(
            step=step_name
        ).set(0)

        logging.exception(
            "[FAILED] %s after %.3f sec",
            step_name,
            duration,
        )

        return False, duration


def get_cart_button(driver):
    def find_cart_link(d):
        elements = d.find_elements(
            By.XPATH,
            "//a[contains(@href, 'basket.html')]",
        )

        for element in elements:
            if element.is_displayed():
                return element

        return False

    return WebDriverWait(
        driver,
        SELENIUM_TIMEOUT,
    ).until(
        find_cart_link
    )


def get_cart_count(driver):
    cart_button = get_cart_button(
        driver
    )

    text = cart_button.text.strip()

    match = re.search(
        r"(\d+)\s*item",
        text,
        re.IGNORECASE,
    )

    if not match:
        raise RuntimeError(
            f"Unable to parse cart count from: {text!r}"
        )

    return int(match.group(1))


def wait_for_cart_count(
    driver,
    expected_count,
):
    def condition(d):
        try:
            return (
                get_cart_count(d)
                == expected_count
            )
        except Exception:
            return False

    WebDriverWait(
        driver,
        SELENIUM_TIMEOUT,
    ).until(
        condition
    )


def open_cart(driver):
    cart_button = get_cart_button(
        driver
    )

    logging.info(
        "Opening cart: %s",
        cart_button.text,
    )

    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center'});",
        cart_button,
    )

    driver.execute_script(
        "arguments[0].click();",
        cart_button,
    )

    WebDriverWait(
        driver,
        SELENIUM_TIMEOUT,
    ).until(
        EC.url_contains(
            "basket.html"
        )
    )

    WebDriverWait(
        driver,
        SELENIUM_TIMEOUT,
    ).until(
        EC.presence_of_element_located(
            (
                By.ID,
                "cart-list",
            )
        )
    )


def find_remove_links(driver):
    links = driver.find_elements(
        By.XPATH,
        "//tbody[@id='cart-list']"
        "//a[contains(@onclick, 'deleteFormCart')]",
    )

    return [
        link
        for link in links
        if link.is_displayed()
    ]


def remove_one_cart_row(driver):
    remove_links = find_remove_links(
        driver
    )

    if not remove_links:
        raise RuntimeError(
            "No remove icon found in cart"
        )

    remove_button = remove_links[0]

    logging.info(
        "Found remove icon: %s",
        remove_button.get_attribute(
            "onclick"
        ),
    )

    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center'});",
        remove_button,
    )

    driver.execute_script(
        "arguments[0].click();",
        remove_button,
    )

    logging.info(
        "Remove icon clicked"
    )


def click_update_basket(driver):
    update_button = WebDriverWait(
        driver,
        SELENIUM_TIMEOUT,
    ).until(
        EC.presence_of_element_located(
            (
                By.XPATH,
                "//a[contains(@onclick, 'updateCart()')]",
            )
        )
    )

    logging.info(
        "Clicking Update basket"
    )

    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center'});",
        update_button,
    )

    driver.execute_script(
        "arguments[0].click();",
        update_button,
    )


def prepare_empty_cart(driver):
    current_count = get_cart_count(
        driver
    )

    logging.info(
        "Precondition: current cart count = %s",
        current_count,
    )

    if current_count == 0:
        logging.info(
            "Precondition complete: 0 items in cart"
        )
        return

    max_iterations = 50

    for _ in range(max_iterations):
        if "basket.html" not in driver.current_url:
            open_cart(driver)

        remove_links = find_remove_links(
            driver
        )

        if not remove_links:
            current_count = get_cart_count(
                driver
            )

            if current_count == 0:
                break

            raise RuntimeError(
                "Cart count is non-zero, but no remove links found"
            )

        remove_one_cart_row(
            driver
        )

        click_update_basket(
            driver
        )

        try:
            wait_for_cart_count(
                driver,
                0,
            )
            break

        except TimeoutException:
            current_count = get_cart_count(
                driver
            )

            logging.info(
                "Cart still contains %s item(s), continuing cleanup",
                current_count,
            )
    else:
        raise RuntimeError(
            "Unable to empty cart within 50 iterations"
        )

    final_count = get_cart_count(
        driver
    )

    if final_count != 0:
        raise RuntimeError(
            f"Cart cleanup failed, final count={final_count}"
        )

    logging.info(
        "Precondition complete: 0 items in cart"
    )


def step_home(driver):
    try:
        driver.get(
            TARGET_URL
        )

    except TimeoutException:
        logging.warning(
            "Page load timeout, checking current page state"
        )

    WebDriverWait(
        driver,
        SELENIUM_TIMEOUT,
    ).until(
        lambda d: "WeaveSocks" in d.title
    )

    logging.info(
        "Verified page title: %s",
        driver.title,
    )


def step_auth(driver):
    login_link = WebDriverWait(
        driver,
        SELENIUM_TIMEOUT,
    ).until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                "//a[normalize-space()='Login']",
            )
        )
    )

    login_link.click()

    WebDriverWait(
        driver,
        SELENIUM_TIMEOUT,
    ).until(
        EC.visibility_of_element_located(
            (
                By.ID,
                "login-modal",
            )
        )
    )

    username = WebDriverWait(
        driver,
        SELENIUM_TIMEOUT,
    ).until(
        EC.visibility_of_element_located(
            (
                By.ID,
                "username-modal",
            )
        )
    )

    password = WebDriverWait(
        driver,
        SELENIUM_TIMEOUT,
    ).until(
        EC.visibility_of_element_located(
            (
                By.ID,
                "password-modal",
            )
        )
    )

    username.clear()
    username.send_keys(
        USERNAME
    )

    password.clear()
    password.send_keys(
        PASSWORD
    )

    login_button = WebDriverWait(
        driver,
        SELENIUM_TIMEOUT,
    ).until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                "//div[@id='login-modal']"
                "//button[contains(normalize-space(), 'Log in')]",
            )
        )
    )

    login_button.click()

    WebDriverWait(
        driver,
        SELENIUM_TIMEOUT,
    ).until(
        EC.text_to_be_present_in_element(
            (
                By.TAG_NAME,
                "body",
            ),
            "Logged in as Test User",
        )
    )

    logging.info(
        "Authentication verified"
    )


def step_catalogue(driver):
    catalogue_link = WebDriverWait(
        driver,
        SELENIUM_TIMEOUT,
    ).until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                "//a[normalize-space()='Catalogue']",
            )
        )
    )

    catalogue_link.click()

    WebDriverWait(
        driver,
        SELENIUM_TIMEOUT,
    ).until(
        EC.url_contains(
            "category.html"
        )
    )

    WebDriverWait(
        driver,
        SELENIUM_TIMEOUT,
    ).until(
        EC.presence_of_element_located(
            (
                By.ID,
                "products",
            )
        )
    )

    WebDriverWait(
        driver,
        SELENIUM_TIMEOUT,
    ).until(
        EC.presence_of_element_located(
            (
                By.XPATH,
                "//div[@id='products']"
                "//h3/a[normalize-space()='Colourful']",
            )
        )
    )

    logging.info(
        "Product 'Colourful' found"
    )


def step_add_to_cart(driver):
    product = WebDriverWait(
        driver,
        SELENIUM_TIMEOUT,
    ).until(
        EC.presence_of_element_located(
            (
                By.XPATH,
                "//div[@id='products']"
                "//div[contains(@class, 'product')]"
                "[.//h3/a[normalize-space()='Colourful']]",
            )
        )
    )

    add_button = product.find_element(
        By.XPATH,
        ".//p[contains(@class, 'buttons')]"
        "//a[contains(normalize-space(), 'Add to cart')]",
    )

    WebDriverWait(
        driver,
        SELENIUM_TIMEOUT,
    ).until(
        lambda d: (
            add_button.is_displayed()
            and add_button.is_enabled()
        )
    )

    add_button.click()

    logging.info(
        "Clicked Add to cart"
    )

    wait_for_cart_count(
        driver,
        1,
    )

    logging.info(
        "Verified cart indicator: 1 item(s) in cart"
    )


def step_cart(driver):
    wait_for_cart_count(
        driver,
        1,
    )

    open_cart(
        driver
    )

    product_rows = WebDriverWait(
        driver,
        SELENIUM_TIMEOUT,
    ).until(
        lambda d: d.find_elements(
            By.XPATH,
            "//tbody[@id='cart-list']"
            "//tr[contains(@class, 'item')]"
            "[.//a[normalize-space()='Colourful']]",
        )
    )

    if len(product_rows) != 1:
        raise AssertionError(
            "Expected exactly one Colourful cart row, "
            f"got {len(product_rows)}"
        )

    product_row = product_rows[0]

    quantity_input = product_row.find_element(
        By.XPATH,
        ".//input[@type='number']",
    )

    quantity = quantity_input.get_attribute(
        "value"
    )

    if quantity != "1":
        raise AssertionError(
            f"Expected quantity=1, got {quantity!r}"
        )

    price_cells = product_row.find_elements(
        By.XPATH,
        ".//td[normalize-space()='$18.00']",
    )

    if len(price_cells) < 2:
        raise AssertionError(
            "Expected unit price and total both equal $18.00"
        )

    logging.info(
        "Cart verified: Colourful x1, $18.00"
    )


def step_remove_from_cart(driver):
    remove_links = find_remove_links(
        driver
    )

    if not remove_links:
        raise RuntimeError(
            "Remove icon was not found"
        )

    remove_button = remove_links[0]

    logging.info(
        "Found remove icon: %s",
        remove_button.get_attribute(
            "onclick"
        ),
    )

    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center'});",
        remove_button,
    )

    driver.execute_script(
        "arguments[0].click();",
        remove_button,
    )

    logging.info(
        "Remove icon clicked"
    )


def step_update_basket(driver):
    click_update_basket(
        driver
    )

    wait_for_cart_count(
        driver,
        0,
    )

    logging.info(
        "Basket successfully emptied: 0 items in cart"
    )


def step_logout(driver):
    logout_button = WebDriverWait(
        driver,
        SELENIUM_TIMEOUT,
    ).until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                "//a[normalize-space()='Logout']",
            )
        )
    )

    logout_button.click()

    logging.info(
        "Logout completed"
    )


def run_transaction():
    driver = None
    transaction_sum = 0.0
    overall_success = True
    authenticated = False
    cart_opened = False

    clear_previous_metrics()

    try:
        driver = create_driver()

        success, duration = execute_step(
            "home",
            lambda: step_home(driver),
        )

        transaction_sum += duration

        if not success:
            overall_success = False

        if overall_success:
            success, duration = execute_step(
                "auth",
                lambda: step_auth(driver),
            )

            transaction_sum += duration

            if success:
                authenticated = True
            else:
                overall_success = False

        if overall_success and authenticated:
            try:
                prepare_empty_cart(
                    driver
                )
            except Exception:
                logging.exception(
                    "Cart precondition failed"
                )
                overall_success = False

        if overall_success:
            success, duration = execute_step(
                "catalogue",
                lambda: step_catalogue(driver),
            )

            transaction_sum += duration

            if not success:
                overall_success = False

        if overall_success:
            success, duration = execute_step(
                "add_to_cart",
                lambda: step_add_to_cart(driver),
            )

            transaction_sum += duration

            if not success:
                overall_success = False

        if overall_success:
            success, duration = execute_step(
                "cart",
                lambda: step_cart(driver),
            )

            transaction_sum += duration

            if success:
                cart_opened = True
            else:
                overall_success = False

        transaction_duration.set(
            transaction_sum
        )

        transaction_success.set(
            1 if overall_success else 0
        )

        logging.info(
            "TRANSACTION RESULT: %s",
            "SUCCESS" if overall_success else "FAILURE",
        )

        logging.info(
            "N0 TRANSACTION DURATION: %.3f sec",
            transaction_sum,
        )

        logging.info(
            "N0 TRANSACTION DURATION: %.3f ms",
            transaction_sum * 1000,
        )

    except WebDriverException:
        logging.exception(
            "WebDriver error"
        )

        transaction_success.set(0)

    except Exception:
        logging.exception(
            "Unexpected transaction error"
        )

        transaction_success.set(0)

    finally:
        if authenticated:
            try:
                if not cart_opened:
                    current_count = get_cart_count(
                        driver
                    )

                    if current_count > 0:
                        open_cart(
                            driver
                        )

                current_remove_links = find_remove_links(
                    driver
                )

                if current_remove_links:
                    remove_success, _ = execute_step(
                        "remove_from_cart",
                        lambda: step_remove_from_cart(driver),
                    )

                    if remove_success:
                        while find_remove_links(
                            driver
                        ):
                            extra_remove_success, _ = execute_step(
                                "remove_from_cart",
                                lambda: step_remove_from_cart(driver),
                            )

                            if not extra_remove_success:
                                break

                        if not find_remove_links(
                            driver
                        ):
                            execute_step(
                                "update_basket",
                                lambda: step_update_basket(driver),
                            )

            except Exception:
                logging.exception(
                    "Cleanup failed"
                )

            execute_step(
                "logout",
                lambda: step_logout(driver),
            )

        try:
            push_to_gateway(
                PUSHGATEWAY_URL,
                job=JOB_NAME,
                registry=registry,
            )

            logging.info(
                "Metrics successfully pushed to Pushgateway"
            )

        except Exception:
            logging.exception(
                "Failed to push metrics to Pushgateway"
            )

        if driver is not None:
            try:
                driver.quit()
            except Exception:
                logging.exception(
                    "Error while closing browser"
                )


if __name__ == "__main__":
    logging.info(
        "Starting WeaveSocks synthetic monitor"
    )

    while True:
        run_transaction()

        logging.info(
            "Waiting %s seconds before next check...",
            CHECK_INTERVAL,
        )

        time.sleep(
            CHECK_INTERVAL
        )
