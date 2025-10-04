import datetime
import io
import uuid
from array import array

import pytest

# Test data is now provided via fixtures in conftest.py


def test_touch(temp_fs):
    fs = temp_fs
    assert not fs.exists("/newfile")
    fs.touch("/newfile")
    assert fs.exists("/newfile")


@pytest.mark.asyncio(loop_scope="function")
async def test_async_touch(temp_afs):
    fs = temp_afs
    assert not await fs._exists("/newfile")
    await fs._touch("/newfile")
    assert await fs._exists("/newfile")


def test_rm(temp_nested_fs):
    fs = temp_nested_fs
    assert fs.exists("/emptyfile")
    fs.rm("/emptyfile")
    assert not fs.exists("/emptyfile")
    assert fs.exists("/nested/nested2/file1")
    fs.rm("/nested", recursive=True)
    assert not fs.exists("/nested/nested2/file1")
    assert not fs.exists("/nested/nested2")
    assert not fs.exists("/nested")


@pytest.mark.asyncio(loop_scope="function")
async def test_async_rm(temp_nested_afs):
    fs = temp_nested_afs
    assert await fs._exists("/emptyfile")
    await fs._rm("/emptyfile")
    assert not await fs._exists("/emptyfile")
    assert await fs._exists("/nested/nested2/file1")
    await fs._rm("/nested", recursive=True)
    assert not await fs._exists("/nested/nested2/file1")
    assert not await fs._exists("/nested/nested2")
    assert not await fs._exists("/nested")


def test_rm_file(temp_fs):
    fs = temp_fs
    fs.touch("/file1")
    assert fs.exists("/file1")
    fs.rm_file("/file1")
    assert not fs.exists("/file1")


@pytest.mark.asyncio(loop_scope="function")
async def test_async_rm_file(temp_afs):
    fs = temp_afs
    await fs._touch("/file1")
    assert await fs._exists("/file1")
    await fs._rm_file("/file1")
    assert not await fs._exists("/file1")


def test_bulk_rm(temp_fs):
    fs = temp_fs
    files = ["/file1", "/file2", "/file3"]
    for file in files:
        fs.touch(file)
    fs.rm(files)
    for file in files:
        assert not fs.exists(file)


@pytest.mark.asyncio(loop_scope="function")
async def test_async_bulk_rm(temp_afs):
    fs = temp_afs
    files = ["/file1", "/file2", "/file3"]
    for file in files:
        await fs._touch(file)
    await fs._rm(files)
    for file in files:
        assert not await fs._exists(file)


def test_rmdir(temp_nested_fs):
    fs = temp_nested_fs
    assert fs.exists("/emptyfile")
    with pytest.raises(FileNotFoundError, match=r"Directory not found: .*\/emptyfile"):
        fs.rmdir("/emptyfile")

    assert fs.exists("/nested/nested2/file1")
    with pytest.raises(OSError, match=r"Directory not empty: .*\/nested"):
        fs.rmdir("/nested/nested2")

    assert not fs.exists("/unknwon")
    with pytest.raises(FileNotFoundError):
        fs.rmdir("/unknwon")
    fs.rm("/nested/nested2/file1")
    fs.rm("/nested/nested2/file2")

    fs.rmdir("/nested/nested2")
    assert not fs.exists("/nested/nested2")


@pytest.mark.asyncio(loop_scope="function")
async def test_async_rmdir(temp_nested_afs):
    fs = temp_nested_afs
    assert await fs._exists("/emptyfile")
    with pytest.raises(FileNotFoundError, match=r"Directory not found: .*\/emptyfile"):
        await fs._rmdir("/emptyfile")

    assert await fs._exists("/nested/nested2/file1")
    with pytest.raises(OSError, match=r"Directory not empty: .*\/nested"):
        await fs._rmdir("/nested/nested2")

    assert not await fs._exists("/unknwon")
    with pytest.raises(FileNotFoundError):
        await fs._rmdir("/unknwon")
    await fs._rm("/nested/nested2/file1")
    await fs._rm("/nested/nested2/file2")

    await fs._rmdir("/nested/nested2")
    assert not await fs._exists("/nested/nested2")


def test_mkdir(temp_fs):
    fs = temp_fs
    assert not fs.exists("/newdir")
    fs.mkdir("/newdir")
    assert fs.exists("/newdir")
    assert "/newdir" in fs.ls("/", detail=False)
    nested_path = "/newdir/nested/subnested"
    assert not fs.exists("/newdir/nested/")
    with pytest.raises(
        FileNotFoundError, match=r"Parent directory does not exists: .*\/newdir\/nested"
    ):
        fs.mkdir(nested_path, create_parents=False)
    fs.mkdir(nested_path, create_parents=True)
    assert fs.exists(nested_path)


@pytest.mark.asyncio(loop_scope="function")
async def test_async_mkdir(temp_afs):
    fs = temp_afs
    assert not await fs._exists("/newdir")
    await fs._mkdir("/newdir")
    assert await fs._exists("/newdir")
    assert "/newdir" in await fs._ls("/", detail=False)
    nested_path = "/newdir/nested/subnested"
    assert not await fs._exists("/newdir/nested/")
    with pytest.raises(
        FileNotFoundError, match=r"Parent directory does not exists: .*\/newdir\/nested"
    ):
        await fs._mkdir(nested_path, create_parents=False)
    await fs._mkdir(nested_path, create_parents=True)
    assert await fs._exists(nested_path)


def test_makedirs(temp_fs):
    fs = temp_fs
    assert not fs.exists("/newdir")
    fs.makedirs("/newdir")
    assert fs.exists("/newdir")
    assert "/newdir" in fs.ls("/", detail=False)
    nested_path = "/newdir/nested/subnested"
    assert not fs.exists("/newdir/nested/")
    fs.makedirs(nested_path)
    assert fs.exists(nested_path)

    with pytest.raises(FileExistsError, match=r"Directory already exists: .*\/newdir"):
        fs.makedirs("/newdir")


@pytest.mark.asyncio(loop_scope="function")
async def test_async_makedirs(temp_afs):
    fs = temp_afs
    assert not await fs._exists("/newdir")
    await fs._makedirs("/newdir")
    assert await fs._exists("/newdir")
    assert "/newdir" in await fs._ls("/", detail=False)
    nested_path = "/newdir/nested/subnested"
    assert not await fs._exists("/newdir/nested/")
    await fs._makedirs(nested_path)
    assert await fs._exists(nested_path)

    with pytest.raises(FileExistsError, match=r"Directory already exists: .*\/newdir"):
        await fs._makedirs("/newdir")


def test_copy(temp_fs):
    fs = temp_fs
    with fs.open("/file1.txt", "wb") as f:
        f.write(b"hello world")
    fs.copy("/file1.txt", "/file2.txt")
    assert fs.cat("/file1.txt") == fs.cat("/file2.txt")


def test_get_item_id_from_file_handler(temp_fs):
    fs = temp_fs
    with fs.open("/file1.txt", "wb") as f:
        item_id = f.get_item_id()
        assert item_id is None
        f.write(b"hello world")
    # at the end of the with block the file is closed and item_id should be available
    item_id = f.get_item_id()
    assert item_id is not None
    assert isinstance(item_id, str)

    # on an already existing file the item_id should be available right away
    with fs.open("/file1.txt", "wb") as f:
        item_id = f.get_item_id()
        assert item_id is not None
        assert isinstance(item_id, str)


@pytest.mark.asyncio(loop_scope="function")
async def test_async_get_item_id_from_file_handler(temp_afs):
    fs = temp_afs
    async with await fs._open_async("/file1.txt", "wb") as f:
        item_id = await f._get_item_id()
        assert item_id is None
        await f.write(b"hello world")
    item_id = await f._get_item_id()
    assert item_id is not None
    assert isinstance(item_id, str)

    async with await fs._open_async("/file1.txt", "wb") as f:
        item_id = await f._get_item_id()
        assert item_id is not None
        assert isinstance(item_id, str)


@pytest.mark.asyncio(loop_scope="function")
async def test_async_copy(temp_afs):
    fs = temp_afs
    await fs._pipe_file("/file1.txt", b"hello world")
    await fs._copy("/file1.txt", "/file2.txt")
    assert await fs._cat("/file1.txt") == await fs._cat("/file2.txt")


def test_copy_recursive(temp_fs):
    fs = temp_fs
    fs.makedirs("/orig/nested")
    fs.touch("/orig/nested/file1.txt")
    fs.copy("/orig", "/dest", recursive=True)
    assert fs.exists("/dest")
    assert fs.exists("/dest/nested")
    assert fs.exists("/dest/nested/file1.txt")


@pytest.mark.asyncio(loop_scope="function")
async def test_async_copy_recursive(temp_afs):
    fs = temp_afs
    await fs._makedirs("/orig/nested")
    await fs._touch("/orig/nested/file1.txt")
    await fs._copy("/orig", "/dest", recursive=True)
    assert await fs._exists("/dest")
    assert await fs._exists("/dest/nested")
    assert await fs._exists("/dest/nested/file1.txt")


def test_move(temp_fs):
    fs = temp_fs
    fs.pipe_file("/file1.txt", b"hello world")
    fs.move("/file1.txt", "/file2.txt")
    assert not fs.exists("/file1.txt")
    assert fs.exists("/file2.txt")
    assert fs.cat("/file2.txt") == b"hello world"

    fs.makedirs("/orig/nested")
    fs.move("file2.txt", "/orig/nested/")
    assert not fs.exists("/file2.txt")
    assert fs.exists("/orig/nested/file2.txt")
    assert fs.cat("/orig/nested/file2.txt") == b"hello world"


@pytest.mark.asyncio(loop_scope="function")
async def test_async_move(temp_afs):
    fs = temp_afs
    await fs._pipe_file("/file1.txt", b"hello world")
    await fs._move("/file1.txt", "/file2.txt")
    assert not await fs._exists("/file1.txt")
    assert await fs._exists("/file2.txt")
    assert await fs._cat("/file2.txt") == b"hello world"

    await fs._makedirs("/orig/nested")
    await fs._move("file2.txt", "/orig/nested/")
    assert not await fs._exists("/file2.txt")
    assert await fs._exists("/orig/nested/file2.txt")
    assert await fs._cat("/orig/nested/file2.txt") == b"hello world"


def test_read_block(temp_fs, all_test_data):
    fs = temp_fs
    data = all_test_data["files"]["test/accounts.1.json"]
    lines = io.BytesIO(data).readlines()
    path = "/test.csv"
    fs.pipe_file(path, data)
    assert fs.read_block(path, 1, 35, b"\n") == lines[1]
    assert fs.read_block(path, 0, 30, b"\n") == lines[0]
    assert fs.read_block(path, 0, 35, b"\n") == lines[0] + lines[1]
    assert fs.read_block(path, 0, 5000, b"\n") == data
    assert len(fs.read_block(path, 0, 5)) == 5
    assert len(fs.read_block(path, 4, 5000)) == len(data) - 4
    assert fs.read_block(path, 5000, 5010) == b""


def test_write_small(temp_fs):
    fs = temp_fs
    with fs.open("/test.csv", "wb") as f:
        f.write(b"hello world")
    assert fs.cat("/test.csv") == b"hello world"


@pytest.mark.asyncio(loop_scope="function")
async def test_async_write_small(temp_afs):
    fs = temp_afs
    async with await fs._open_async("/test.csv", "wb") as f:
        await f.write(b"hello world")
    assert await fs._cat("/test.csv") == b"hello world"


def test_write_large(temp_fs):
    fs = temp_fs
    mb = 2**20  # 1 MB
    payload = b"0" * mb
    block_size = (2**10) * 320  # 320 KB the mmap block size for msgraph
    path = "/test.csv"
    with fs.open(path, "wb", block_size=block_size) as f:
        f.write(payload)
    assert fs.cat(path) == payload


@pytest.mark.asyncio(loop_scope="function")
async def test_async_write_large(temp_afs):
    fs = temp_afs
    mb = 2**20
    payload = b"0" * mb
    block_size = (2**10) * 320
    path = "/test.csv"
    async with await fs._open_async(path, "wb", block_size=block_size) as f:
        await f.write(payload)
    assert await fs._cat(path) == payload


def test_write_blocks(temp_fs):
    fs = temp_fs
    mb = 2**20
    payload = b"0" * mb
    block_size = (2**10) * 320
    content_size = len(payload)
    path = "/test.csv"
    chunk_size = 50000
    start = 0
    with fs.open(path, "wb", block_size=block_size) as f:
        # we will write the payload by a block not divisible by the block size
        for i in range(0, content_size, chunk_size):
            f.write(payload[i : i + chunk_size])
            start = i + chunk_size
        # write the remaining bytes
        f.write(payload[start:])
    assert fs.du(path) == content_size


@pytest.mark.asyncio(loop_scope="function")
async def test_async_write_blocks(temp_afs):
    fs = temp_afs
    mb = 2**20
    payload = b"0" * mb
    block_size = (2**10) * 320
    content_size = len(payload)
    path = "/test.csv"
    chunk_size = 50000
    start = 0
    async with await fs._open_async(path, "wb", block_size=block_size) as f:
        for i in range(0, content_size, chunk_size):
            await f.write(payload[i : i + chunk_size])
            start = i + chunk_size
        await f.write(payload[start:])
    assert await fs._du(path) == content_size


def test_open_no_write(temp_fs):
    fs = temp_fs
    with fs.open("/test.csv", "wb") as f:
        assert f.tell() == 0
    assert fs.exists("/test.csv")
    assert fs.cat("/test.csv") == b""


@pytest.mark.asyncio(loop_scope="function")
async def test_async_open_no_write(temp_afs):
    fs = temp_afs
    async with await fs._open_async("/test.csv", "wb") as f:
        assert f.tell() == 0
    assert await fs._exists("/test.csv")
    assert await fs._cat("/test.csv") == b""


def test_append(temp_nested_fs, all_test_data):
    fs = temp_nested_fs
    data = all_test_data["text_files"]["nested/file1"]
    assert fs.cat("/nested/file1") == data
    with fs.open("/nested/file1", "ab") as f:
        assert f.tell() == len(data)  # append, no write, small file
    assert fs.cat("/nested/file1") == data
    with fs.open("/nested/file1", "ab") as f:
        f.write(b"extra")  # append, write, small file
    assert fs.cat("/nested/file1") == data + b"extra"

    bigfile = "/bigfile"
    bigfile_size = 2**20
    bigfile_content = b"a" * bigfile_size
    block_size = (2**10) * 320
    with fs.open(bigfile, "wb") as f:
        f.write(bigfile_content)
    read_content = fs.cat(bigfile)
    read_content_size = len(read_content)
    assert bigfile_size == read_content_size

    with fs.open(bigfile, "ab", block_size=block_size) as f:
        pass  # append, no write, big file
    read_content = fs.cat(bigfile)
    read_content_size = len(read_content)
    assert bigfile_size == read_content_size

    with fs.open(bigfile, "ab", block_size=block_size) as f:
        assert f.tell() == bigfile_size
        f.write(b"extra")  # append, small write, big file
    assert fs.cat(bigfile) == bigfile_content + b"extra"

    bigfile_content_b = b"b" * bigfile_size
    with fs.open(bigfile, "ab", block_size=block_size) as f:
        assert f.tell() == bigfile_size + 5
        f.write(bigfile_content_b)  # append, big write, big file
        assert f.tell() == 2 * bigfile_size + 5
    assert fs.cat(bigfile) == bigfile_content + b"extra" + bigfile_content_b


@pytest.mark.asyncio(loop_scope="function")
async def test_async_append(temp_nested_afs, all_test_data):
    fs = temp_nested_afs
    data = all_test_data["text_files"]["nested/file1"]
    assert await fs._cat("/nested/file1") == data
    async with await fs._open_async("/nested/file1", "ab") as f:
        assert f.tell() == len(data)
    assert await fs._cat("/nested/file1") == data
    async with await fs._open_async("/nested/file1", "ab") as f:
        await f.write(b"extra")
    assert await fs._cat("/nested/file1") == data + b"extra"

    bigfile = "/bigfile"
    bigfile_size = 2**20
    bigfile_content = b"a" * bigfile_size
    block_size = (2**10) * 320
    async with await fs._open_async(bigfile, "wb") as f:
        await f.write(bigfile_content)
    read_content = await fs._cat(bigfile)
    read_content_size = len(read_content)
    assert bigfile_size == read_content_size

    async with await fs._open_async(bigfile, "ab", block_size=block_size) as f:
        pass
    read_content = await fs._cat(bigfile)
    read_content_size = len(read_content)
    assert bigfile_size == read_content_size

    async with await fs._open_async(bigfile, "ab", block_size=block_size) as f:
        assert f.tell() == bigfile_size
        await f.write(b"extra")
    assert await fs._cat(bigfile) == bigfile_content + b"extra"

    bigfile_content_b = b"b" * bigfile_size
    async with await fs._open_async(bigfile, "ab", block_size=block_size) as f:
        assert f.tell() == bigfile_size + 5
        await f.write(bigfile_content_b)
        assert f.tell() == 2 * bigfile_size + 5
    assert await fs._cat(bigfile) == bigfile_content + b"extra" + bigfile_content_b


def test_write_array(temp_fs):
    path = "/test.dat"

    data = array("B", [65] * 1000)

    with temp_fs.open(path, "wb") as f:
        f.write(data)

    with temp_fs.open(path, "rb") as f:
        out = f.read()
        assert out == b"A" * 1000


@pytest.mark.asyncio(loop_scope="function")
async def test_async_write_array(temp_afs):
    path = "/test.dat"

    data = array("B", [65] * 1000)

    async with await temp_afs._open_async(path, "wb") as f:
        await f.write(data)

    async with await temp_afs._open_async(path, "rb") as f:
        out = await f.read()
        assert out == b"A" * 1000


def test_upload_with_prefix(temp_fs, all_test_data):
    fs = temp_fs
    sfs = temp_fs.fs

    data = all_test_data["text_files"]["nested/file1"]
    path = f"msgd://{fs.path}/file1"
    sfs.pipe_file(path, data)
    assert sfs.cat(path) == data


def test_text_io__basic(temp_fs):
    with temp_fs.open("file.txt", "w", encoding="utf-8") as fd:
        fd.write("\u00af\\_(\u30c4)_/\u00af")

    with temp_fs.open("file.txt", "r", encoding="utf-8") as fd:
        assert fd.read() == "\u00af\\_(\u30c4)_/\u00af"


def test_text_io__stream_wrapper_works(temp_fs):
    """Ensure using TextIOWrapper works."""

    with temp_fs.open("file.txt", "wb") as fd:
        fd.write("\u00af\\_(\u30c4)_/\u00af".encode("utf-16-le"))

    with temp_fs.open("file.txt", "rb") as fd:
        with io.TextIOWrapper(fd, "utf-16-le") as stream:
            assert stream.readline() == "\u00af\\_(\u30c4)_/\u00af"


def test_modified(temp_fs):
    fs = temp_fs
    path = "/test.csv"
    fs.touch(path)
    modified = fs.modified(path)
    assert modified is not None
    assert isinstance(modified, datetime.datetime)


@pytest.mark.asyncio(loop_scope="function")
async def test_async_modified(temp_afs):
    fs = temp_afs
    path = "/test.csv"
    await fs._touch(path)
    modified = await fs._modified(path)
    assert modified is not None
    assert isinstance(modified, datetime.datetime)


def test_created(temp_fs):
    fs = temp_fs
    path = "/test.csv"
    fs.touch(path)
    created = fs.created(path)
    assert created is not None
    assert isinstance(created, datetime.datetime)


@pytest.mark.asyncio(loop_scope="function")
async def test_async_created(temp_afs):
    fs = temp_afs
    path = "/test.csv"
    await fs._touch(path)
    created = await fs._created(path)
    assert created is not None
    assert isinstance(created, datetime.datetime)


def test_cat_ranges(temp_fs):
    data = b"a string to select from"
    path = "/parts"
    temp_fs.pipe(path, data)

    assert temp_fs.cat_file(path) == data
    assert temp_fs.cat_file(path, start=5) == data[5:]
    assert temp_fs.cat_file(path, end=5) == data[:5]
    assert temp_fs.cat_file(path, start=1, end=-1) == data[1:-1]
    assert temp_fs.cat_file(path, start=-5) == data[-5:]


@pytest.mark.asyncio(loop_scope="function")
async def test_async_cat_ranges(temp_afs):
    data = b"a string to select from"
    path = "/parts"
    await temp_afs._pipe_file(path, data)

    assert await temp_afs._cat_file(path) == data
    assert await temp_afs._cat_file(path, start=5) == data[5:]
    assert await temp_afs._cat_file(path, end=5) == data[:5]
    assert await temp_afs._cat_file(path, start=1, end=-1) == data[1:-1]
    assert await temp_afs._cat_file(path, start=-5) == data[-5:]


def test_default_rm_no_trash(temp_fs):
    fs = temp_fs
    unique_name = str(uuid.uuid4())
    path = f"/{unique_name}"
    fs.touch(path)
    assert fs.exists(path)
    fs.rm(path)
    assert not fs.exists(path)
    recycle_bin_items = {i["name"] for i in fs.fs.get_recycle_bin_items()}
    assert unique_name not in recycle_bin_items


def test_rm_to_trash(temp_fs):
    fs = temp_fs
    fs.fs.use_recycle_bin = True
    unique_name = str(uuid.uuid4())
    path = f"/{unique_name}"
    fs.touch(path)
    assert fs.exists(path)
    fs.rm(path)
    assert not fs.exists(path)
    recycle_bin_items = {i["name"] for i in fs.fs.get_recycle_bin_items()}
    assert unique_name in recycle_bin_items

    fs.fs.use_recycle_bin = False
    unique_name = str(uuid.uuid4())
    path = f"/{unique_name}"
    fs.touch(path)
    assert fs.exists(path)
    fs.rm(path)
    assert not fs.exists(path)
    recycle_bin_items = {i["name"] for i in fs.fs.get_recycle_bin_items()}
    assert unique_name not in recycle_bin_items


def test_lock_unlock(temp_fs):
    fs = temp_fs
    path = "/test.csv"
    fs.touch(path)
    versions = fs.get_versions(path)
    assert len(versions) == 1
    fs.checkout(path)
    fs.checkin(path, comment="my update comment")
    versions = fs.get_versions(path)
    assert len(versions) == 2


def test_set_properties(temp_fs):
    fs = temp_fs
    path = "/test.csv"
    fs.touch(path)
    properties = {"_ExtendedDescription": "My Description"}
    fs.set_properties(path, properties)
    item = fs.info(path)
    assert item["item_info"].get("description") == "My Description"


@pytest.mark.asyncio(loop_scope="function")
async def test_async_set_properties(temp_afs):
    fs = temp_afs
    path = "/test.csv"
    await fs._touch(path)
    properties = {"_ExtendedDescription": "My other Description"}
    await fs._set_properties(path, properties)
    item = await fs._info(path)
    assert item["item_info"].get("description") == "My other Description"
