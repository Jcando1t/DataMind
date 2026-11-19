import React from 'react';
import ReactDOM from 'react-dom/client';
import { ConfigProvider } from 'antd';
import 'antd/dist/reset.css';
import App from './App.jsx';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ConfigProvider theme={{ token: { colorPrimary: '#3568e8', colorText: '#18243b', colorBorder: '#e1e7f1', borderRadius: 6, fontFamily: '"Segoe UI", "Microsoft YaHei", sans-serif' } }}>
      <App />
    </ConfigProvider>
  </React.StrictMode>,
);
