import { copyFile, mkdir, mkdtemp, rm, symlink } from 'node:fs/promises'
import { join, resolve } from 'node:path'

const names = [
  'prints unique ID', 'uses size', 'uses alphabet',
  'shows an error on unknown argument', 'shows an error if size is not a number',
  'requires error if size is a negative number',
  ...['0.5', '1.5', 'Infinity'].map(size => `rejects non-integer size ${size}`),
  'displays help', 'displays version'
]
const violations = new Set(names.slice(6, 9))
const results = new Map()
let directory
let summary
let suite
let problem
try {
  const { run } = await import('node:test')
  directory = await mkdtemp(join(process.env.MENTAT_ARTIFACTS_DIR, 'native-'))
  await mkdir(join(directory, 'test'))
  const file = join(directory, 'test', 'bin.test.mjs')
  await copyFile(join(process.env.MENTAT_CHECKS_DIR, 'base', 'test', 'bin.test.js'), file)
  await symlink(resolve('bin'), join(directory, 'bin'), 'dir')
  for await (const event of run({ files: [file] })) {
    if (event.type === 'test:summary') summary = event.data
    if (event.type !== 'test:pass' && event.type !== 'test:fail') continue
    const data = event.data
    if (data?.details?.type === 'suite' && data.name === 'CLI' && data.nesting === 0 && !suite) {
      suite = event
    } else if (data?.details?.type === 'test' && data.nesting === 1 && names.includes(data.name) && !results.has(data.name)) {
      results.set(data.name, event)
    } else {
      throw new Error('unexpected or duplicate test result')
    }
  }
  const counts = summary?.counts
  const failed = [...results.values()].filter(event => event.type === 'test:fail')
  if (results.size !== names.length || !suite || !counts || counts.tests !== names.length ||
      counts.passed !== names.length - failed.length || counts.failed !== failed.length ||
      counts.cancelled !== 0 || counts.skipped !== 0 || counts.todo !== 0 || counts.suites !== 1 ||
      summary.success !== (failed.length === 0) || suite.type !== (failed.length ? 'test:fail' : 'test:pass') ||
      (failed.length && suite.data.details.error?.failureType !== 'subtestsFailed')) {
    throw new Error('incomplete test run')
  }
  for (const event of results.values()) {
    if (event.data.skip || event.data.todo) throw new Error('skipped or todo test')
    if (event.type === 'test:fail') {
      const error = event.data.details.error
      if (!violations.has(event.data.name) || error?.failureType !== 'testCodeFailure' ||
          error.cause?.code !== 'ERR_ASSERTION' || error.cause?.message !== 'Missing expected rejection.') {
        throw new Error(`unexpected failure: ${event.data.name}`)
      }
    }
  }
} catch (error) {
  problem = error.message
} finally {
  if (directory) {
    try { await rm(directory, { recursive: true, force: true }) } catch (error) { problem = error.message }
  }
}
const failures = [...results.values()].filter(event => event.type === 'test:fail').map(event => event.data.name)
const outcome = problem ? 'incomplete' : failures.length ? 'violation' : 'pass'
console.log(JSON.stringify({ node: process.version, passed: results.size - failures.length, failed: failures.length, failures, outcome, ...(problem && { error: problem }) }))
// Node can exit 1 before the tests run, so the contract reserves 10 for violations.
process.exitCode = problem ? 2 : failures.length ? 10 : 0
