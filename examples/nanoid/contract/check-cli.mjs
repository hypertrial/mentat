import { spawnSync } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'

try {
  const results = []
  let outcome = 0
  for (const input of ['0.5', '1.5', 'Infinity', '7']) {
    const result = spawnSync(process.execPath, ['bin/nanoid.js', '--size', input], {
      encoding: 'utf8', timeout: 5000, maxBuffer: 1024 * 1024
    })
    const stdout = result.stdout ?? ''
    const stderr = result.stderr ?? ''
    results.push({ input, status: result.status, stderr, stdout_length: stdout.length })
    if (result.error || result.signal || result.status === null ||
        /^(?:[ \t]+at |node:internal\/)/m.test(stderr)) {
      outcome = 2
    } else {
      const matches = input === '7'
        ? result.status === 0 && stderr === '' && /^[A-Za-z0-9_-]{7}\n$/.test(stdout)
        : result.status === 1 && stderr === 'Size must be positive integer\n' && stdout === ''
      if (!matches && outcome === 0) outcome = 1
    }
  }
  writeFileSync(join(process.env.MENTAT_ARTIFACTS_DIR, 'witness.json'), JSON.stringify(results, null, 2) + '\n')
  process.exitCode = outcome
} catch (error) {
  console.error(error.message)
  process.exitCode = 2
}
