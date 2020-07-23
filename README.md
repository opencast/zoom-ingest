#Opencast-Zoom-Ingester

**Requirements**

python packages:

`pika`  
`requests`  
`wget`
`zoomus`

`RabbitMQ Server`

For local tests:

`ngrok`

**Installation**

The packages above can all be installed via *pip*   
*ngrok* can be downloaded from https://ngrok.com/download and must be executed in the source folder

**Usage**

First the Webhook must be activated in Zoom. The Webhook server is either running 
locally on the computer or on a server. If you choose to run it locally you need to make your server public as Zoom does
not accept local URL's. This is done using *ngrok*.

First the python server must be started. Navigate to the source folder:

`python3 -m zoom_webhook 8080`

starts the server on *localhost:8080*

then navigate into the *ngrok* folder

with

`./ngrok http 8080`

the local server is being forwarded to ngrok

Afterwards you get the URL where the server is publicly accessible

It should look like this i.e.

`https://f8d68f6f3073.ngrok.io/`

Afterwards the webhook must be activated on zoom. To do this, go to the Marketplace in Zoom. In the upper right corner select Develop Build App and click on Webhook Only. Just fill in all the necessary information.
A new Event Subscription will now be added. Select "All Recordings have completed" as event type. Enter the URL of ngrok or from your server. Finally activate the app.
 
This activates the Webhook. Finally, the Python script for upload/download must be started.
 
 `python3 uploader.py `
 