import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or os.urandom(32).hex()
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(BASE_DIR, "ecg_management.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # DICOM MWL Server
    MWL_AE_TITLE = "MWL"
    MWL_PORT = 6701

    # DICOM Store SCP
    STORE_AE_TITLE = "ECG_STORE"
    STORE_PORT = 6702

    # DICOM storage path
    DICOM_STORAGE_DIR = os.path.join(BASE_DIR, "dicom_storage")
