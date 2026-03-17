// Stealth script to bypass basic bot detection 
// Adapted from puppeteer-extra-plugin-stealth
const applyStealth = () => {
  // Override webdriver
  Object.defineProperty(navigator, 'webdriver', {
    get: () => false,
  });

  // Mock Chrome properties
  window.chrome = {
    runtime: {},
    app: {
      InstallState: {
        DISABLED: 'disabled',
        INSTALLED: 'installed',
        NOT_INSTALLED: 'not_installed'
      },
      RunningState: {
        CANNOT_RUN: 'cannot_run',
        READY_TO_RUN: 'ready_to_run',
        RUNNING: 'running'
      },
      getDetails: function() {},
      getIsInstalled: function() {}
    }
  };

  // Override permissions
  const originalQuery = window.navigator.permissions.query;
  window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
      Promise.resolve({ state: Notification.permission }) :
      originalQuery(parameters)
  );

  // Mock languages
  Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en'],
  });

  // Mock plugins
  Object.defineProperty(navigator, 'plugins', {
    get: () => {
      const plugins = [
        {
          name: 'Chrome PDF Plugin',
          filename: 'internal-pdf-viewer',
          description: 'Portable Document Format'
        },
        {
          name: 'Chrome PDF Viewer',
          filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
          description: ''
        },
        {
          name: 'Native Client',
          filename: 'internal-nacl-plugin',
          description: ''
        }
      ];
      // Create a mock PluginArray
      const pluginArray = Object.create(PluginArray.prototype);
      Object.assign(pluginArray, plugins);
      Object.defineProperty(pluginArray, 'length', { value: plugins.length });
      return pluginArray;
    }
  });
};

applyStealth();
