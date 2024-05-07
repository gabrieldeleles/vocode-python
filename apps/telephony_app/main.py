# Standard library imports
import logging
import os
import sys
import random
from datetime import datetime
from functools import partial
from typing import Optional

# Third-party imports
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Response
from vocode.streaming.models.telephony import TwilioConfig

# from pyngrok import ngrok
from vocode.streaming.telephony.config_manager.redis_config_manager import (
    RedisConfigManager,
)
from vocode.streaming.models.agent import ChatGPTAgentConfig
from vocode.streaming.models.message import BaseMessage
from vocode.streaming.models.telephony import TwilioCallConfig
from vocode.streaming.telephony.server.base import (
    AbstractInboundCallConfig,
    TwilioInboundCallConfig,
    TelephonyServer,
)
from vocode.streaming.utils import create_conversation_id

# Local application/library specific imports
from speller_agent import (
    SpellerAgentFactory,
    SpellerAgentConfig,
)


# if running from python, this will load the local .env
# docker-compose will load the .env file by itself
load_dotenv()

app = FastAPI(docs_url=None)

logging.basicConfig()
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

config_manager = RedisConfigManager(
    logger=logger,
)

BASE_URL = os.getenv("BASE_URL")

# if not BASE_URL:
#     ngrok_auth = os.environ.get("NGROK_AUTH_TOKEN")
#     if ngrok_auth is not None:
#         ngrok.set_auth_token(ngrok_auth)
#     port = sys.argv[sys.argv.index("--port") + 1] if "--port" in sys.argv else 3000

#     # Open a ngrok tunnel to the dev server
#     BASE_URL = ngrok.connect(port).public_url.replace("https://", "")
#     logger.info('ngrok tunnel "{}" -> "http://127.0.0.1:{}"'.format(BASE_URL, port))

# if not BASE_URL:
#     raise ValueError("BASE_URL must be set in environment if not using pyngrok")


class CustomTelephonyServer(TelephonyServer):
    async def generate_prompt_preamble(
        self, color: Optional[str] = None, twilio_from: Optional[str] = None
    ) -> str:
        """
        Generates a prompt preamble, including a greeting that acknowledges the caller's phone number if provided.
        If a color is not provided, it selects a random color from a predefined list.

        Args:
            color (Optional[str]): The color to generate a prompt for. Defaults to None.
            twilio_from (Optional[str]): The phone number of the caller. Defaults to None.

        Returns:
            str: A string containing the prompt preamble.
        """
        # Select a random color if none is provided
        if color is None:
            color = random.choice(["red", "blue", "green", "yellow", "purple"])

        # Determine the current time of day
        date_now = datetime.now()
        hour = date_now.hour
        time_of_day = "morning" if hour < 12 else "afternoon"

        # Generate the greeting message, including the caller's phone number if available
        greeting = (
            f"Hi, thanks for calling from {twilio_from}. " if twilio_from else "Hi, "
        )

        # Return the complete prompt preamble
        return f"{greeting} It's currently {time_of_day} on {date_now.strftime('%A, %B %d, %Y')}. Let me tell you facts and interesting tidbits about {color}."

    def create_inbound_route(self, inbound_call_config: AbstractInboundCallConfig):
        async def twilio_route(
            twilio_config: TwilioConfig,
            twilio_sid: str = Form(alias="CallSid"),
            twilio_from: str = Form(alias="From"),
            twilio_to: str = Form(alias="To"),
        ) -> Response:
            # Dynamically generate the initial message for each call
            dynamic_initial_message = await self.generate_prompt_preamble(
                twilio_from=twilio_from
            )

            # Modify the agent config to use the dynamic initial message
            inbound_call_config.agent_config.initial_message = BaseMessage(
                text=dynamic_initial_message
            )

            call_config = TwilioCallConfig(
                transcriber_config=inbound_call_config.transcriber_config
                or TwilioCallConfig.default_transcriber_config(),
                agent_config=inbound_call_config.agent_config,
                synthesizer_config=inbound_call_config.synthesizer_config
                or TwilioCallConfig.default_synthesizer_config(),
                twilio_config=twilio_config,
                twilio_sid=twilio_sid,
                from_phone=twilio_from,
                to_phone=twilio_to,
            )

            conversation_id = create_conversation_id()
            await self.config_manager.save_config(conversation_id, call_config)
            return self.templater.get_connection_twiml(
                base_url=self.base_url, call_id=conversation_id
            )

        if isinstance(inbound_call_config, TwilioInboundCallConfig):
            self.logger.info(
                f"Set up inbound call TwiML at https://{self.base_url}{inbound_call_config.url}"
            )
            return partial(twilio_route, inbound_call_config.twilio_config)
        else:
            raise ValueError(
                f"Unknown inbound call config type {type(inbound_call_config)}"
            )


telephony_server = CustomTelephonyServer(
    base_url=BASE_URL,
    config_manager=config_manager,
    inbound_call_configs=[
        TwilioInboundCallConfig(
            url="/inbound_call/{color}",
            agent_config=ChatGPTAgentConfig(
                initial_message=BaseMessage(
                    text="Placeholder, will be replaced dynamically"
                ),
                prompt_preamble="Have a pleasant conversation about the color randomly choosed",
                generate_responses=True,
            ),
            # uncomment this to use the speller agent instead
            # agent_config=SpellerAgentConfig(
            #     initial_message=BaseMessage(text="im a speller agent, say something to me and ill spell it out for you"),
            #     generate_responses=False,
            # ),
            twilio_config=TwilioConfig(
                account_sid=os.environ["TWILIO_ACCOUNT_SID"],
                auth_token=os.environ["TWILIO_AUTH_TOKEN"],
            ),
            logger=logger,
        )
    ],
    # agent_factory=SpellerAgentFactory(),
    logger=logger,
)

app.include_router(telephony_server.get_router())
