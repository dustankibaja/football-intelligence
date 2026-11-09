export default function handler(request, response) {
  response.setHeader('Cache-Control', 'no-store');
  return response.status(200).json({
    service: 'footmo-private-trial',
    providerCredentialConfigured: Boolean(process.env.LIVE_DATA_API_KEY),
    privateStorageConfigured: Boolean(process.env.BLOB_READ_WRITE_TOKEN),
    schedulerSecretConfigured: Boolean(process.env.CRON_SECRET),
    automationEnabled: process.env.AUTOMATION_ENABLED !== 'false',
    scientificCertification: 'NOT_CERTIFIED_PROVISIONAL_POISSON'
  });
}
