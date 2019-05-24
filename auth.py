from os.path import expanduser
import configparser
import boto.sts
import boto.s3
import base64
import xml.etree.ElementTree as ET
import getpass
import time
import os
import asyncio
from pyppeteer import launch

async def main():
    # region: The default AWS region that this script will connect
    # to for all API calls
    region = os.environ.get('AWS_REGION')

    # output format: The AWS CLI output format that will be configured in the
    # saml profile (affects subsequent CLI calls)
    outputformat = os.environ.get('AWS_OUTPUT_FORMAT')

    # awsconfigfile: The file where this script will store the temp
    # credentials under the saml profile
    awsconfigfile = '/.aws/credentials'

    # Load or create AWS config file
    home = expanduser("~")
    filename = home + awsconfigfile

    # Read in the existing config file
    config = configparser.ConfigParser()
    config.read(filename)

    # If aws config doesn't exist, create one because boto requires it
    if not config.has_section('default'):
        config.add_section('default')
        config.set('default', 'output', outputformat)
        config.set('default', 'region', region)
        config.set('default', 'aws_access_key_id', '')
        config.set('default', 'aws_secret_access_key', '')
        # Write the updated config file
        with open(filename, 'w+') as configfile:
            config.write(configfile)

    browser = await launch(
        headless=True,
        executablePath="/usr/bin/chromium-browser",
        args=['--no-sandbox', '--disable-gpu']
    )
    page = await browser.newPage()
    await page.goto(os.environ.get('AWS_LOGIN_URL'))

    print("Username: ", end='')
    username = input()
    password = getpass.getpass()
    print('')

    await page.focus('#j_username')
    await page.keyboard.type(username)
    await page.focus('#j_password')
    await page.keyboard.type(password)
    await page.evaluate("document.querySelector('button[type=submit]').click()")
    time.sleep(5)

    print('The "push" command was sent to your phone by Duo. You have about 60 seconds to react.')

    res = await page._client.send("Page.getFrameTree")
    childFrames = res["frameTree"]["childFrames"]
    duo_id = next(
        frame["frame"]["id"]
        for frame in childFrames
        if "duosecurity.com" in frame["frame"]["url"]
    )

    duo = page._frameManager.frame(duo_id)
    await duo.evaluate("document.querySelector('button').click()")
    await page.waitForNavigation({ 'waitUntil': 'networkidle0', 'timeout': 60000 })

    samlElement = await page.waitForSelector('input[name=SAMLResponse]')
    samlValueProperty = await samlElement.getProperty('value')
    samlValue = await samlValueProperty.jsonValue()

    # await page.screenshot({'path': 'example.png'})
    await browser.close()

    # Overwrite and delete the credential variables, just for safety
    username = '##############################################'
    password = '##############################################'
    del username
    del password

    awsroles = []
    root = ET.fromstring(base64.b64decode(samlValue))
    for saml2attribute in root.iter('{urn:oasis:names:tc:SAML:2.0:assertion}Attribute'):
        if (saml2attribute.get('Name') == 'https://aws.amazon.com/SAML/Attributes/Role'):
            for saml2attributevalue in saml2attribute.iter('{urn:oasis:names:tc:SAML:2.0:assertion}AttributeValue'):
                awsroles.append(saml2attributevalue.text)

    # Note the format of the attribute value should be role_arn,principal_arn
    # but lots of blogs list it as principal_arn,role_arn so let's reverse
    # them if needed
    for awsrole in awsroles:
        chunks = awsrole.split(',')
        if'saml-provider' in chunks[0]:
            newawsrole = chunks[1] + ',' + chunks[0]
            index = awsroles.index(awsrole)
            awsroles.insert(index, newawsrole)
            awsroles.remove(awsrole)

    # If I have more than one role, ask the user which one they want,
    # otherwise just proceed
    print("")
    if len(awsroles) > 1:
        i = 0
        print("Please choose the role you would like to assume:")
        for awsrole in awsroles:
            print('[', i, ']: ', awsrole.split(',')[0])
            i += 1
        print("Selection: ", end="")
        selectedroleindex = input()

        # Basic sanity check of input
        if int(selectedroleindex) > (len(awsroles) - 1):
            print('You selected an invalid role index, please try again')
            sys.exit(0)

        role_arn = awsroles[int(selectedroleindex)].split(',')[0]
        principal_arn = awsroles[int(selectedroleindex)].split(',')[1]
    else:
        role_arn = awsroles[0].split(',')[0]
        principal_arn = awsroles[0].split(',')[1]

    # Use the assertion to get an AWS STS token using Assume Role with SAML
    conn = boto.sts.connect_to_region(region)
    token = conn.assume_role_with_saml(role_arn, principal_arn, samlValue)

    config.set('default', 'aws_access_key_id', token.credentials.access_key)
    config.set('default', 'aws_secret_access_key', token.credentials.secret_key)
    config.set('default', 'aws_session_token', token.credentials.session_token)

    # Write the updated config file
    with open(filename, 'w+') as configfile:
        config.write(configfile)

    # Give the user some basic info as to what has just happened
    print('\n\n----------------------------------------------------------------')
    print('Your new access key pair has been stored in the AWS configuration file {0} under the default profile.'.format(filename))
    print('Note that it will expire at {0}.'.format(token.credentials.expiration))
    print('After this time, you may the following command to refresh your access key pair:')
    print('shib-auth')
    print('----------------------------------------------------------------\n\n')


asyncio.get_event_loop().run_until_complete(main())
