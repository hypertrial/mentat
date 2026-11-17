import { spawnSync } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'

try {
  const args = process.argv.slice(2)
  const control = args[0] === '--negative-control' && args.length === 1
  if (args.length && !control) throw new Error('unexpected arguments')
  const results = []
  let outcome = 0
  for (const input of ['0.5', '1.5', 'Infinity', '7']) {
    const result = spawnSync(process.execPath, ['bin/nanoid.js', '--size', input], {
      encoding: 'utf8', timeout: 5000, maxBuffer: 1024 * 1024
    })
    const stdout = result.stdout ?? ''
    const stderr = result.stderr ?? ''
    const unusable = result.error || result.signal || result.status === null ||
      /^(?:[ \t]+at |node:internal\/)/m.test(stderr)
    const synthetic = !unusable && control && input === '0.5'
    const observed = synthetic
      ? { status: 0, stderr: '', stdout: 'incorrect-id\n' }
      : { status: result.status, stderr, stdout }
    results.push({
      input, status: observed.status, stderr: observed.stderr, stdout_length: observed.stdout.length,
      ...(synthetic ? { synthetic: true } : {})
    })
    if (unusable) {
      outcome = 2
    } else {
      const matches = input === '7'
        ? observed.status === 0 && observed.stderr === '' && /^[A-Za-z0-9_-]{7}\n$/.test(observed.stdout)
        : observed.status === 1 && observed.stderr === 'Size must be positive integer\n' && observed.stdout === ''
      if (!matches && outcome === 0) outcome = 1
    }
  }
  writeFileSync(join(process.env.MENTAT_ARTIFACTS_DIR, 'witness.json'), JSON.stringify(results, null, 2) + '\n')
  process.exitCode = outcome
} catch (error) {
  console.error(error.message)
  process.exitCode = 2
}
