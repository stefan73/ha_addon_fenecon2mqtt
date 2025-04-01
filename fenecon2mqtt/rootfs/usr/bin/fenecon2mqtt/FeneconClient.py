#!/usr/bin/python3
import json
import logging
import os
import time
import uuid

import config
import rel
import websocket
from jsonrpcclient import Ok, parse_json, request_json
from publish_hassio_discovery import publish_hassio_discovery


class FeneconClient:
    version = None
    # Static uuids for request
    uuid_str_auth = str(uuid.uuid4())
    uuid_str_getEdge = str(uuid.uuid4())
    uuid_str_getEdgeConfig_payload = str(uuid.uuid4())
    uuid_str_getEdgeConfig_request = str(uuid.uuid4())
    uuid_str_subscribe_payload = str(uuid.uuid4())
    uuid_str_subscribe_request = str(uuid.uuid4())
    #uuid_str_getComponentChannels_payload = str(uuid.uuid4())
    #uuid_str_getComponentChannels_req = str(uuid.uuid4()

    # JSON request templates
    json_auth_passwd = request_json("authenticateWithPassword", params={"password":config.fenecon['fems_password']}, id=uuid_str_auth)
    json_get_edge = request_json("getEdge", params={"edgeId":"0"}, id=uuid_str_getEdge)
    json_get_edgeconfig_payload = request_json("getEdgeConfig", params={" ": " "}, id=uuid_str_getEdgeConfig_payload)
    json_get_edgeconfig_req = request_json("edgeRpc", params={"edgeId":"0", "payload":json.loads(json_get_edgeconfig_payload)}, id=uuid_str_getEdgeConfig_request)
    json_subscribe_payload = request_json("subscribeChannels", params={"count":"0", "channels":config.channels2subscribe}, id=uuid_str_subscribe_payload)
    json_subscribe_req = request_json("edgeRpc", params={"edgeId":"0", "payload":json.loads(json_subscribe_payload)}, id=uuid_str_subscribe_request)
    #json_get_componentChannels_payload = request_json("getChannelsOfComponent", params={"componentId": "ess0", "channelId": "_sum"}, id=uuid_str_getComponentChannels_payload)
    #json_get_componentChannels_req = request_json("edgeRpc", params={"edgeId":"0", "payload":json.loads(json_get_componentChannels_payload)}, id=uuid_str_getComponentChannels_req)

    def __init__(self, mqtt):
        logger = logging.getLogger(__name__)
        logger.info('Init')
        self.mqtt = mqtt
        self.connect_retry_counter = 0
        self.connect_retry_max = 10
        self.connect_websocket()

    def is_docker(self):
        path = '/proc/self/cgroup'
        return (
            os.path.exists('/.dockerenv') or
            os.path.isfile(path) and any('docker' in line for line in open(path))
        )

    def connect_websocket(self):
        logger = logging.getLogger(__name__)
        logger.info('Connect to Fenecons websocket')

        ws_uri = str(f"ws://{config.fenecon['fems_ip']}:8085/websocket")
        ws = websocket.WebSocketApp(ws_uri ,
                                         on_open=self.on_open,
                                         on_message=self.on_message,
                                         on_error=self.on_error,
                                         on_close=self.on_close)
        ws.run_forever(dispatcher=rel)
        rel.signal(2, rel.abort)  # Keyboard Interrupt
        rel.dispatch()

    def on_message(self, ws, message):
        logger = logging.getLogger(__name__)
        logger.debug("on_message")
        msg_dict = json.loads(message)

        msg_id = msg_dict.get('id')
        msg_curent_data = None
        #msg_dict.get('params'],{}).get('payload', {}).get('method')

        try:
            msg_curent_data = msg_dict['params']['payload']['params']
        except KeyError:
            msg_curent_data = None

        if msg_dict.get('id') is None and msg_curent_data:
            # process subscribed data
            keys = list(msg_curent_data.keys())
            for key in keys:
                hassio_uid = str(f"{config.hassio['sensor_uid_prefix']}{key}").replace("/", "-")
                # Use not retained messages for sensor values
                self.mqtt.publish((config.hassio['mqtt_broker_hassio_queue']+ "/" + hassio_uid).lower(), str(msg_curent_data[key]))

        elif msg_id == self.uuid_str_auth:
            # process authorization reqest
            # {'jsonrpc': '2.0', 'id': '3f56cce8-553f-4075-890e-30d00a61e2ca', 'error': {'code': 1003, 'message': 'Authentication failed', 'data': []}}
            if msg_dict.get('error') is None:
                logger.info("FEMS Authentication successfull")
                return

            error_code = msg_dict['error']['code']
            error_msg = msg_dict['error']['message']
            logger.error(f"FEMS Authentication failed. Error ({error_code}): {error_msg}")
            logger.error('Wait 5 seconds. Shut down. Let Watchdog restart this add-on.')
            time.sleep(5)
            quit()
        elif msg_id == self.uuid_str_getEdge:
            logger.info('getEdge received')
            # process edge data
            try:
                self.version = msg_dict['result']['edge']['version']
            except Exception:
                self.version = "N/A"
            return
        elif msg_id == self.uuid_str_getEdgeConfig_request:
            # process edge configuration data
            logger.info("Edgeconfig received -> purge old Homeassistant discovery topic")
            self.mqtt.clear_ha_discovery_topic()

            logger.debug("Edgeconfig received -> publish new Homeassistant discovery topic")
            # Iterate over all components and ask for their channels
            #for comp in config.fenecon['fems_request_components']:
            #    print(comp)
            #logger.warning(self.json_get_componentChannels_req)
            #ws.send(self.json_get_componentChannels_req)

            publish_hassio_discovery(self.mqtt, msg_dict, self.version)
            if self.is_docker():
                logger.info("Dump Fenecon configuration to local docker filesystem")
                try:
                    with open('/share/fenecon/fenecon_config.json', 'w') as fp:
                        json.dump(msg_dict, fp)
                except Exception:
                    logger.error("Dump Fenecon configration to local docker filesystem failed")
            return
        time.sleep(0.8)
        #elif msg_id == self.uuid_str_getComponentChannels_req:
        #    logger.info("Channel received for component -> purge old Homeassistant discovery topic")
        #    return

    def on_error(self, ws, error):
        logger = logging.getLogger(__name__)
        logger.error(f'Fenecon connection error: {error}')
        logger.error('Wait 5 seconds. Shut down. Let Watchdog restart this add-on.')
        time.sleep(5)
        quit()

    def on_close(self, ws, close_status_code, close_msg):
        logger = logging.getLogger(__name__)
        logger.warning(f'Fenecon sonnection closed. Code:    {close_status_code}')
        logger.warning(f'                           Message: {close_msg}')
        #logger.warning('try again in 30 seconds')
        rel.abort()
        logger.warning('Wait 5 seconds. Shut down. Let Watchdog restart this add-on.')
        time.sleep(5)
        quit()

    def on_open(self, ws):
        logger = logging.getLogger(__name__)
        self.connect_retry_counter = 0
        # auth
        logger.debug('Fenecon opened connection -> send authenticate')
        ws.send(self.json_auth_passwd)
        time.sleep(0.5)
        # get edge
        logger.debug('Fenecon opened connection -> send getEdge')
        ws.send(self.json_get_edge)
        time.sleep(0.5)
        # get edgeConfig
        logger.debug('Fenecon opened connection -> send getEdge configuration')
        ws.send(self.json_get_edgeconfig_req)
        time.sleep(0.5)
        # Subscribe
        logger.debug('Fenecon opened connection -> send subscribe')
        ws.send(self.json_subscribe_req)
