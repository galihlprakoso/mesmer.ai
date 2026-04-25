import { mount } from 'svelte'
import './app.css'
import App from './App.svelte'
import { init as initRouter } from './lib/router.js'

initRouter()

const app = mount(App, {
  target: document.getElementById('app'),
})

export default app
