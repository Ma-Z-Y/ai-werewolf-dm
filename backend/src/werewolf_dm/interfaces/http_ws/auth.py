from werewolf_dm.application.rooms import TokenRecord, TokenService


def resolve_socket_token(token_service: TokenService, raw_token: str) -> TokenRecord:
    return token_service.resolve(raw_token)
