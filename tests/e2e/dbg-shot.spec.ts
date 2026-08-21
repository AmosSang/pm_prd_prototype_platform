import { test } from '@playwright/test'

test('debug upload 400', async ({ page }) => {
  const events: string[] = []
  page.on('console', (m) => events.push('console: ' + m.text()))
  page.on('pageerror', (e) => events.push('pageerror: ' + e.message))
  await page.goto('/demo/shot')
  await page.waitForTimeout(3000)
  await page.click('button.shot')
  await page.waitForTimeout(8000)
  const logs = await page.locator('[data-testid="msg-logs"] li').allTextContents()
  console.log('=== MSG LOGS ===')
  console.log(logs.join('\n'))
  console.log('=== BROWSER EVENTS ===')
  console.log(events.join('\n') || '(none)')
})
