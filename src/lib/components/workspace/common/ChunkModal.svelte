<script>
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import Modal from '$lib/components/common/Modal.svelte';
	import FileChunks from './FileChunks.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	export let show = false;
	// export let accessControl = {};
	// export let allowPublic = true;

	export let selectedFileId = '';
	export let knowledgeId = '';

	// export let onChange = () => {};
</script>

<Modal size="lg" bind:show> <!--className="rounded-2xl bg-white"-->
	<div>
		<div class=" flex justify-between dark:text-gray-100 px-5 pt-3 pb-1">
			<div class=" text-lg font-medium self-center font-primary">
				{$i18n.t('Chunks')}
			</div>
			<button
				class="self-center"
				on:click={() => {
					show = false;
				}}
			>
				<XMark className={'size-5'} />
			</button>
		</div>

		<div class="w-full px-5 pb-4 dark:text-white">
			<FileChunks {selectedFileId} {knowledgeId}/>
		</div>
	</div>
</Modal>
