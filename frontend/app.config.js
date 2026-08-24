module.exports = ({ config }) => ({
  ...config,
  extra: {
    ...config.extra,
    // The production API is a private HTTPS capability URL supplied at bundle
    // time. Keeping it out of app.json prevents accidental recommits or a
    // fallback to the unauthenticated plain-HTTP listener.
    apiUrl: process.env.ALIF_API_URL ?? config.extra?.apiUrl,
  },
});
