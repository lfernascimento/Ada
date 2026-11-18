import React from 'react'; import {createRoot} from 'react-dom/client'; import '@fontsource-variable/google-sans'; import App from './App'; import './style.css'; import './theme.css'; import './modern.css';
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
