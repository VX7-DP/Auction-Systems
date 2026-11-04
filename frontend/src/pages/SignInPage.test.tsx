import { fireEvent, screen, within } from '@testing-library/react'
import { expect, test } from 'vitest'
import { setupApp } from '../test/setupApp.tsx'

test('needs a name, then signs in and returns to the next page', async () => {
  setupApp('/sign-in?next=%2Fadmin')
  fireEvent.click(await screen.findByRole('button', { name: 'Start bidding' }))
  expect(await screen.findByText('Enter a display name.')).toBeTruthy()

  fireEvent.change(screen.getByLabelText('Display name'), { target: { value: '  Asha  ' } })
  fireEvent.click(screen.getByRole('button', { name: 'Start bidding' }))

  expect(await screen.findByRole('heading', { name: 'Create an auction' })).toBeTruthy()
  expect(window.location.pathname).toBe('/admin')
  expect(within(screen.getByRole('banner')).getByText('Asha')).toBeTruthy()
})

test('never redirects to another site', async () => {
  setupApp('/sign-in?next=https%3A%2F%2Fevil.example')
  fireEvent.change(await screen.findByLabelText('Display name'), { target: { value: 'Asha' } })
  fireEvent.click(screen.getByRole('button', { name: 'Start bidding' }))

  expect(await screen.findByRole('heading', { name: 'Live auctions' })).toBeTruthy()
  expect(window.location.pathname).toBe('/')
})

test('the header links signed-out guests to sign in and back', async () => {
  setupApp('/admin')
  const link = await screen.findByRole('link', { name: 'Sign in' })
  expect(link.getAttribute('href')).toBe('/sign-in?next=%2Fadmin')
})
