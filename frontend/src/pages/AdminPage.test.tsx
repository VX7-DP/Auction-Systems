import { fireEvent, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import { setupApp } from '../test/setupApp.tsx'

function fill(password: string, title = 'Vintage camera', price = '1500') {
  fireEvent.change(screen.getByLabelText('Admin password'), { target: { value: password } })
  fireEvent.change(screen.getByLabelText('Title'), { target: { value: title } })
  fireEvent.change(screen.getByLabelText('Opening price'), { target: { value: price } })
}

test('a wrong password is reported', async () => {
  setupApp('/admin')
  fill('wrong')
  fireEvent.click(screen.getByRole('button', { name: 'Create auction' }))
  expect(await screen.findByText('Wrong admin password.')).toBeTruthy()
})

test('checks the fields before sending', async () => {
  const { backend } = setupApp('/admin')
  fill('admin', '   ', 'abc')
  fireEvent.change(screen.getByLabelText('Ends'), { target: { value: 'custom' } })
  fireEvent.change(screen.getByLabelText('End date and time (your time zone)'), {
    target: { value: '2001-01-01T10:00' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Create auction' }))

  expect(await screen.findByText('Enter a title.')).toBeTruthy()
  expect(screen.getByText('Enter an amount, like 1500.')).toBeTruthy()
  expect(screen.getByText('The end time must be in the future.')).toBeTruthy()
  expect(backend.requests.filter((r) => r.path === '/api/admin/auctions')).toHaveLength(0)
})

test('creates an auction and links to it', async () => {
  const { backend } = setupApp('/admin')
  fill('admin')
  fireEvent.change(screen.getByLabelText('Ends'), { target: { value: '15' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create auction' }))

  expect(await screen.findByText('Auction created')).toBeTruthy()
  const sent = backend.requests.find((r) => r.path === '/api/admin/auctions')?.body as {
    title: string
    start_price: number
    end_at: string
  }
  expect(sent.title).toBe('Vintage camera')
  expect(sent.start_price).toBe(150_000)
  expect(Date.parse(sent.end_at) - Date.now()).toBeGreaterThan(14 * 60_000)

  fireEvent.click(screen.getByRole('link', { name: 'Open auction' }))
  expect(await screen.findByRole('heading', { level: 1, name: 'Vintage camera' })).toBeTruthy()
})
