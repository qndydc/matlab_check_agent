"""
Description: Start the local MATLAB analysis FastAPI server.
References: interfaces.api.app and uvicorn.
Referenced By: matlab-refactor-web console script.
"""

import uvicorn


def main() -> None:
    uvicorn.run(
        "matlab_refactor_agent.interfaces.api.app:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    main()
