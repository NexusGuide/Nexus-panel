// Everything that identifies this fork in the UI lives here, so rebranding is
// one file rather than a hunt through components.
//
// An empty string means "this project has no such link": the sidebar entry,
// footer line or help icon that would point at it is not rendered at all,
// rather than shown pointing at nothing. Fill one in and it reappears.

export const BRAND_NAME = 'Nexus Panel'

export const REPO_URL = 'https://github.com/NexusGuide/Nexus-panel'
export const ORGANIZATION_URL = 'https://github.com/NexusGuide'

// optional - leave empty until the project actually has one
export const DONATION_URL = ''
export const DISCUSSION_GROUP = ''
export const DOCUMENTATION = ''

// Where the update banner looks for a newer release. It reads this fork's
// releases, not upstream's: comparing our own 0.0.1 against upstream's 5.3.0
// told every owner they were years out of date. Until this repo publishes a
// release the API returns 404 and no banner is shown, which is correct.
export const RELEASES_API_URL = 'https://api.github.com/repos/NexusGuide/Nexus-panel/releases/latest'

// what the banner tells an owner to run
export const UPDATE_COMMAND = 'sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/NexusGuide/Nexus-panel/main/install.sh)" @ update'
