// Read-only browser navigation regression, including Community Cloud's outer frame.
// NODE_PATH=<node_modules> CHROMIUM_EXECUTABLE=<chrome> node tests/browser/education-cloud.cjs
// EDUCATION_TEST_URL may point to a local native Streamlit server. No paid actions.
const assert = require('assert/strict');
const {chromium} = require('playwright');
const base = process.env.EDUCATION_TEST_URL || 'https://stock-sentinel-dev.streamlit.app';
const timeout = Number(process.env.EDUCATION_WAIT_MS || 60000);
const crumbs = '.ed-crumbs, [class*="st-key-ed_native_crumbs_"]';
const routes = {
  Education: ['Education', '/Education'],
  publisher: ['AI Ed Shorts', '/AI_Ed_Shorts'],
  privacy: ['AI Ed Shorts Publisher Privacy Policy', '/AI_Ed_Shorts_Privacy'],
  contact: ['Contact', '/Contact'],
};
async function visibleFrame(page, heading) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    for (const frame of page.frames()) {
      if (await frame.getByRole('heading', {name: heading, exact: true}).isVisible().catch(() => false)) return frame;
    }
    await page.waitForTimeout(200);
  }
  throw Error(`Heading not visible: ${heading}; outer path=${new URL(page.url()).pathname}`);
}
async function at(page, key) {
  const [heading, path] = routes[key];
  const frame = await visibleFrame(page, heading);
  // A heading inside the Cloud app iframe is insufficient: bookmark URL must match.
  await page.waitForURL(url => url.pathname === path, {timeout: 15000}).catch(() => {
    throw Error(`Content "${heading}" loaded, but outer URL stayed ${new URL(page.url()).pathname}; expected ${path}`);
  });
  assert.equal(await frame.locator('[data-testid="stException"]').count(), 0);
  console.log(`PASS heading + outer URL ${path}`);
  return frame;
}
async function click(page, key, label, index = 0, selector = null) {
  const frame = await at(page, key);
  const scope = selector ? frame.locator(selector) : frame;
  await scope.getByRole('link', {name: label, exact: true}).nth(index).click();
}
async function external(page, key, label, expected, selector = null) {
  const frame = await at(page, key);
  const scope = selector ? frame.locator(selector) : frame;
  const link = scope.getByRole('link', {name: label, exact: true});
  assert.equal(await link.getAttribute('href'), expected);
  const opened = page.context().waitForEvent('page', {timeout: 15000});
  await link.click();
  const tab = await opened;
  await tab.waitForLoadState('domcontentloaded', {timeout});
  const destination = new URL(tab.url());
  const allowed = new URL(expected).hostname === 'myaccount.google.com'
    ? ['myaccount.google.com', 'accounts.google.com'] : [new URL(expected).hostname];
  assert(allowed.includes(destination.hostname), `Unexpected external host: ${destination.hostname}`);
  assert((await tab.locator('body').innerText()).trim().length > 0, 'External destination must render');
  if (destination.hostname === 'about.thestocksentinel.com') {
    assert.equal(destination.pathname, new URL(expected).pathname);
    const heading = destination.pathname === '/terms/' ? 'Using Stock Sentinel' : 'What we store, and what we never see';
    await tab.getByRole('heading', {name: heading, exact: true}).waitFor({timeout});
  }
  await tab.close();
  await at(page, key);
  console.log(`PASS external ${label}: popup rendered on ${destination.hostname}`);
}
(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport: {width: Number(process.env.EDUCATION_WIDTH || 1440), height: 960}});
    await page.goto(base + '/Education');
    await click(page, 'Education', 'Explore AI Ed Shorts');
    await at(page, 'publisher');
    await page.reload(); await at(page, 'publisher');
    await external(page, 'publisher', 'Stock Sentinel Terms', 'https://about.thestocksentinel.com/terms/');
    await click(page, 'publisher', 'AI Ed Shorts Privacy Policy');
    await at(page, 'privacy');
    await page.reload(); await at(page, 'privacy');
    await external(page, 'privacy', 'Google Account permissions', 'https://myaccount.google.com/permissions');
    await page.goBack(); await at(page, 'publisher');
    await page.goForward(); await at(page, 'privacy');
    await click(page, 'privacy', 'AI Ed Shorts', 0, crumbs); await at(page, 'publisher');
    await click(page, 'publisher', 'AI Ed Shorts Privacy Policy'); await at(page, 'privacy');
    await click(page, 'privacy', 'Back to AI Ed Shorts'); await at(page, 'publisher');
    // Test the second policy link as well as the introductory one.
    await click(page, 'publisher', 'AI Ed Shorts Privacy Policy', 1); await at(page, 'privacy');
    await click(page, 'privacy', 'Contact The Stock Sentinel'); await at(page, 'contact');
    await page.goBack(); await at(page, 'privacy');
    await click(page, 'privacy', 'Education', 0, crumbs); await at(page, 'Education');
    await click(page, 'Education', 'Explore AI Ed Shorts'); await at(page, 'publisher');
    await click(page, 'publisher', 'Contact The Stock Sentinel'); await at(page, 'contact');
    await click(page, 'contact', 'Education', 0, '.st-key-footer_links'); await at(page, 'Education');
    await click(page, 'Education', 'Explore AI Ed Shorts'); await at(page, 'publisher');
    await click(page, 'publisher', 'Education', 0, crumbs); await at(page, 'Education');
    // Verify shared footer navigation returns to the page matching the address bar.
    for (const [label, heading, path] of [['FAQ', 'FAQ', '/FAQ'], ['How it works', 'How Stock Sentinel works', '/How_It_Works'], ['Contact', 'Contact', '/Contact'], ['Trust Center', 'Trust Center', '/Trust_Center']]) {
      await click(page, 'Education', label, 0, '.st-key-footer_links');
      const frame = await visibleFrame(page, heading);
      await page.waitForURL(url => url.pathname === path, {timeout});
      await frame.locator('.st-key-footer_links').getByRole('link', {name: 'Education', exact: true}).click();
      await at(page, 'Education');
      console.log(`PASS footer ${label} and return`);
    }
    await click(page, 'Education', 'Home', 0, crumbs);
    const home = await visibleFrame(page, /Finding short-term opportunities/);
    await page.waitForURL(url => url.pathname === '/Home', {timeout});
    await home.locator('.st-key-footer_links').getByRole('link', {name: 'Education', exact: true}).click();
    await at(page, 'Education');
    await external(page, 'Education', 'Privacy', 'https://about.thestocksentinel.com/privacy/', '.st-key-footer_links');
    await external(page, 'Education', 'Terms', 'https://about.thestocksentinel.com/terms/', '.st-key-footer_links');
    // Direct entry also proves copied links work in a fresh browser tab.
    for (const key of ['publisher', 'privacy']) {
      const tab = await browser.newPage();
      await tab.goto(base + routes[key][1]); await at(tab, key); await tab.close();
    }
    console.log('PASS Education navigation: outer URL, refresh, history, policy links, breadcrumbs, Contact, and public footer');
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
