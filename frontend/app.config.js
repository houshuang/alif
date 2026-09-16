module.exports = ({ config }) => ({
  ...config,
  // Static web releases can share the HTTPS host under its private access path.
  // Leave unset for native OTA and local development.
  ...(process.env.ALIF_WEB_BASE_PATH
    ? {
        experiments: {
          ...config.experiments,
          baseUrl: process.env.ALIF_WEB_BASE_PATH,
        },
      }
    : {}),
  extra: {
    ...config.extra,
    // The production API is a private HTTPS capability URL supplied at bundle
    // time. Keeping it out of app.json prevents accidental recommits or a
    // fallback to the unauthenticated plain-HTTP listener.
    apiUrl: process.env.ALIF_API_URL ?? config.extra?.apiUrl,
  },
});
