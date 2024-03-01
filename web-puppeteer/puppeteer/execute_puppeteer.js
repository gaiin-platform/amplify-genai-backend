const chromium = require('chrome-aws-lambda');
const { getLogger } = require('../common/logging');

const logger = getLogger('execute_puppeteer');

async function scrape(url, script) {
    let browser = null;
    let result = null;

    try {
        browser = await chromium.puppeteer.launch({
            args: chromium.args,
            defaultViewport: chromium.defaultViewport,
            executablePath: await chromium.executablePath,
            headless: chromium.headless,
        });

        const page = await browser.newPage();

        if (url) {
            await page.goto(url, { waitUntil: 'networkidle0' }); // Ensure the page loads completely
        }

        // Evaluate the script on the page
        // Make sure the script argument is a function that returns the value you want
        result = await page.evaluate(new Function(script));

        // logger.debug("Result from Puppeteer:", result); // Log the result for debugging

        await page.close();
        await browser.close();

        return result;
    } catch (error) {
        logger.error("Error during Puppeteer execution:", error);
        throw error;
    } finally {
        // Ensure browser is closed even if there's an error.
        if (browser) await browser.close();
    }

    return result;
}

module.exports = { scrape };